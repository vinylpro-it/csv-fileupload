from processors.base_processor import BaseProcessor
from pathlib import Path
from config.config import ConfigManager
import mysql.connector
from mysql.connector import Error
import csv
import os
import shutil
import tempfile
from typing import List, Dict
import time

class SAVEDProcessor(BaseProcessor):
    def __init__(self, db_handler, email_notifier, logger):
        super().__init__(db_handler, email_notifier, logger)
        self.config_manager = ConfigManager()
        self.connection = None

    def get_table_name(self):
        return "saved"  # یا هر نامی که جدولتون داره، مثلا "saved_orders" اگر جدا باشه

    def connect(self):
        """Establish database connection with retry"""
        max_retries = 3
        retry_delay = 2  # seconds
        for attempt in range(max_retries):
            try:
                self.config = {
                    'host': self.config_manager.get_setting('mysql', 'mysql_server'),
                    'database': self.config_manager.get_setting('mysql', 'mysql_db'),
                    'user': self.config_manager.get_setting('mysql', 'mysql_user'),
                    'password': self.config_manager.get_setting('mysql', 'mysql_pass'),
                    'port': self.config_manager.get_setting('mysql', 'mysql_port'),
                }
                self.connection = mysql.connector.connect(**self.config)
                if self.connection.is_connected():
                    self.logger.info(f"Connected to MySQL database '{self.config['database']}'")
                    return True
            except Error as e:
                self.logger.error(f"Attempt {attempt + 1}/{max_retries} failed to connect to MySQL: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
        self.logger.error("Failed to connect to MySQL after retries")
        return False

    def disconnect(self):
        """Close database connection"""
        if self.connection and self.connection.is_connected():
            self.connection.close()
            self.logger.info("Database connection closed")

    def process(self, file_path: Path, move_dir: Path) -> bool:
        try:
            self.logger.info(f"Processing SAVED file: {file_path}")
            
            if not self.connect():
                return False

            success = self.upload_csv_data(self.get_table_name(), str(file_path))
            
            if success:
                try:
                    destination = move_dir / file_path.name
                    shutil.move(str(file_path), str(destination))
                    self.logger.info(f"Moved file to {destination}")
                except Exception as e:
                    self.logger.error(f"Failed to move file {file_path} to {move_dir}: {str(e)}")
                    return False
            
            return success
            
        except Exception as e:
            self.logger.error(f"Error processing SAVED file {file_path}: {str(e)}")
            return False
        finally:
            self.disconnect()

    def upload_csv_data(self, table_name, csv_file_path):
        """Upload CSV data to the saved table - ساده، بدون چک تکراری، حتما همه رکوردها insert بشن"""
        if not self.connection or not self.connection.is_connected():
            if not self.connect():
                return False
        
        cursor = None
        try:
            # 1. هدرهای مورد انتظار برای این فایل جدید (بر اساس فایل ضمیمه)
            headers = [
                'DATE', 'ORDER NUMBER', 'COMPANY NAME', 'CUSTOMER PO', 'USER'
            ]

            # 2. چک کردن هدرها و در صورت نیاز اضافه کردن
            has_expected_headers = False
            first_line_headers = []
            with open(csv_file_path, 'r', encoding='utf-8') as csvfile:
                first_line = csvfile.readline().strip()
                first_line_headers = [h.strip() for h in first_line.split(',')]
                normalized_first_line = [h.lower().strip() for h in first_line_headers]
                normalized_expected = [h.lower().strip() for h in headers]
                has_expected_headers = normalized_first_line == normalized_expected
                self.logger.info(f"CSV headers: {first_line_headers}, Expected: {headers}, Match: {has_expected_headers}")

            if not has_expected_headers:
                temp_dir = tempfile.gettempdir()
                temp_path = os.path.join(temp_dir, os.path.basename(csv_file_path) + ".tmp")
                
                try:
                    with open(csv_file_path, 'r', encoding='utf-8') as infile, \
                        open(temp_path, 'w', newline='', encoding='utf-8') as outfile:
                        outfile.write(','.join(headers) + '\n')
                        infile.seek(0)
                        outfile.writelines(infile.readlines()[1:])  # بدون هدر قدیمی
                    
                    shutil.move(temp_path, csv_file_path)
                    self.logger.warning(f"Added correct headers to CSV file: {headers}")
                except Exception as e:
                    self.logger.error(f"Error fixing headers for {csv_file_path}: {str(e)}")
                    return False
            
            # 3. ساخت جدول اگر وجود نداشته باشه
            cursor = self.connection.cursor()
            if not self._table_exists(cursor, table_name):
                self.logger.info(f"Table '{table_name}' does not exist, creating it")
                if not self._create_table(table_name, headers):
                    self.logger.error(f"Failed to create table '{table_name}'")
                    return False

            # 4. خواندن همه رکوردها و insert ساده (بدون هیچ چک تکراری)
            rows_to_insert = []
            
            with open(csv_file_path, 'r', encoding='utf-8') as csvfile:
                csvreader = csv.DictReader(csvfile)
                actual_headers = [h.strip() for h in csvreader.fieldnames]
                
                self.logger.info(f"Processing CSV with columns: {actual_headers}")

                for row in csvreader:
                    # تریم کردن فضاهای اضافی
                    complete_row = {}
                    for header in actual_headers:
                        value = row.get(header, '') or ''
                        complete_row[header] = value.strip() if value else ''

                    # هیچ رکوردی skip نمیشه - همه اضافه میشن
                    rows_to_insert.append(complete_row)

            # 5. Insert همه رکوردها (batch)
            rows_inserted = 0
            if rows_to_insert:
                try:
                    db_columns = actual_headers
                    columns = ', '.join([f'`{h}`' for h in db_columns])
                    placeholders = ', '.join(['%s'] * len(db_columns))
                    insert_query = f"INSERT INTO `{table_name}` ({columns}) VALUES ({placeholders})"
                    
                    batch_values = [[row[h] for h in db_columns] for row in rows_to_insert]
                    cursor.executemany(insert_query, batch_values)
                    rows_inserted = cursor.rowcount
                    self.connection.commit()
                    self.logger.info(f"Successfully inserted {rows_inserted} rows into {table_name}")
                
                except Exception as e:
                    self.logger.error(f"Batch insert failed: {str(e)}")
                    self.connection.rollback()
                    return False

            # هیچ نوتیفیکیشن تکراری ارسال نمیشه چون چک نمی کنیم
            return True
        
        except Exception as e:
            self.logger.error(f"Upload failed for {csv_file_path}: {str(e)}")
            if self.connection:
                self.connection.rollback()
            return False
        finally:
            if cursor:
                cursor.close()

    def _create_table(self, table_name, headers):
        """ساخت جدول با TEXT برای همه ستون‌ها + id"""
        cursor = None
        try:
            cursor = self.connection.cursor()
            
            columns = ["id INT NOT NULL AUTO_INCREMENT PRIMARY KEY"]
            for header in headers:
                columns.append(f"`{header}` TEXT NOT NULL DEFAULT ''")
            
            create_sql = f"CREATE TABLE `{table_name}` ({', '.join(columns)})"
            self.logger.debug(f"Executing CREATE TABLE: {create_sql}")
            cursor.execute(create_sql)
            
            self.connection.commit()
            self.logger.info(f"Created table '{table_name}' with columns: {headers}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to create table '{table_name}': {str(e)}")
            return False
        finally:
            if cursor:
                cursor.close()

    def _table_exists(self, cursor, table_name):
        """چک کردن وجود جدول"""
        try:
            cursor.execute(f"SHOW TABLES LIKE %s", (table_name,))
            exists = cursor.fetchone() is not None
            self.logger.debug(f"Table '{table_name}' exists: {exists}")
            return exists
        except Exception as e:
            self.logger.error(f"Error checking table existence: {str(e)}")
            return False