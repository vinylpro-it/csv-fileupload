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

class MULLIONCUTTINGProcessor(BaseProcessor):
    def __init__(self, db_handler, email_notifier, logger):
        super().__init__(db_handler, email_notifier, logger)
        self.config_manager = ConfigManager()
        self.connection = None

    def get_table_name(self):
        return "mullioncutting" #MULLIONCUTTING

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
            self.logger.info(f"Processing MULLIONCUTTING file: {file_path}")
            
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
            self.logger.error(f"Error processing MULLIONCUTTING file {file_path}: {str(e)}")
            return False
        finally:
            self.disconnect()

    def upload_csv_data(self, table_name, csv_file_path):
        """Upload CSV data to the casing table, checking ID occurrences"""
        if not self.connection or not self.connection.is_connected():
            if not self.connect():
                return False
        
        cursor = None
        try:
            # 1. Define expected headers (from CSV)
            csv_headers = [
                'H_W', 'BIN', 'ORDER_LINE','MATERIAL', 'LABEL','ORDER', 'WINDOW', 
                'SIZE', 'ITEM_NAME', 'STOP LINE', 'COMPANY', 'PO', 
                'DATE', 'TIME', 'USER', 'ID'
            ]

            # 2. Define DB Headers: Map 'ID' to '_id' to avoid conflict with PK
            db_headers = ['_id' if h == 'ID' else h for h in csv_headers]

            # 3. Check if CSV file has the expected headers
            has_expected_headers = False
            first_line_headers = []
            with open(csv_file_path, 'r', encoding='utf-8') as csvfile:
                first_line = csvfile.readline().strip()
                first_line_headers = [h.strip() for h in first_line.split(',')]
                normalized_first_line_headers = [h.lower().strip() for h in first_line_headers]
                normalized_expected_headers = [h.lower().strip() for h in csv_headers]
                has_expected_headers = normalized_first_line_headers == normalized_expected_headers
                self.logger.info(f"CSV headers: {first_line_headers}, Expected: {csv_headers}, Match: {has_expected_headers}")

            if not has_expected_headers:
                temp_dir = tempfile.gettempdir()
                temp_path = os.path.join(temp_dir, os.path.basename(csv_file_path) + ".tmp")
                
                try:
                    with open(csv_file_path, 'r', encoding='utf-8') as infile, \
                        open(temp_path, 'w', newline='', encoding='utf-8') as outfile:
                        # Write original CSV headers
                        outfile.write(','.join(csv_headers) + '\n')
                        infile.seek(0)
                        outfile.writelines(infile.readlines())
                    
                    shutil.move(temp_path, csv_file_path)
                    self.logger.warning(f"Added headers to CSV file: {csv_headers}")
                except Exception as e:
                    self.logger.error(f"Error adding headers to {csv_file_path}: {str(e)}")
                    return False
            
            # 4. Create table if it doesn't exist (using DB headers)
            cursor = self.connection.cursor()
            if not self._table_exists(cursor, table_name):
                self.logger.info(f"Table '{table_name}' does not exist, attempting to create")
                if not self._create_table(table_name, db_headers):
                    self.logger.error(f"Failed to create table '{table_name}'")
                    return False

            # 5. Collect all rows and count ID occurrences
            rows_to_insert = []
            id_counts = {}  # Track new ID occurrences
            duplicates = []
            
            with open(csv_file_path, 'r', encoding='utf-8') as csvfile:
                csvreader = csv.DictReader(csvfile)
                actual_headers = [h.strip() for h in csvreader.fieldnames]
                
                self.logger.info(f"Processing CSV with columns: {actual_headers}")

                # Normalize for header comparison
                normalized_headers = [h.lower().strip() for h in csv_headers]
                
                for row in csvreader:
                    row_values = [str(row.get(h, '')).lower().strip() for h in actual_headers]
                    if row_values == normalized_headers:
                        self.logger.warning(f"Skipping duplicate header row: {row_values}")
                        continue
                    
                    try:
                        complete_row = {h: row.get(h, '') or '' for h in actual_headers}
                        # Trim spaces for all columns
                        for header in actual_headers:
                            value = complete_row[header]
                            if value is not None:
                                if value.strip() == '':
                                    complete_row[header] = ''
                                elif value != value.strip():
                                    complete_row[header] = value.strip()

                        # --- CHANGE START ---
                        # Remap 'ID' to '_id' in the row dictionary
                        if 'ID' in complete_row:
                            complete_row['_id'] = complete_row.pop('ID')
                        
                        id_val = complete_row.get('_id', '')
                        # --- CHANGE END ---

                        if not id_val:
                            self.logger.warning(f"Skipping row with missing ID: {complete_row}")
                            continue

                        id_counts[id_val] = id_counts.get(id_val, 0) + 1
                        rows_to_insert.append(complete_row)

                    except Exception as e:
                        self.logger.error(f"Row processing error for row {complete_row}: {str(e)}")
                        continue

            # 6. Check existing ID counts in the database
            if id_counts:
                ids = list(id_counts.keys())
                format_strings = ','.join(['%s'] * len(ids))
                # --- CHANGE START ---
                # Query the '_id' column instead of 'ID'
                query = f"SELECT `_id`, COUNT(*), `DATE` FROM `{table_name}` WHERE `_id` IN ({format_strings}) GROUP BY `_id`"
                # --- CHANGE END ---
                cursor.execute(query, ids)
                existing_counts = {row[0]: row[1] for row in cursor.fetchall()}
                self.logger.debug(f"Existing ID counts: {existing_counts}")

                # Combine existing and new counts, and collect duplicates
                for id_val, new_count in id_counts.items():
                    total_count = new_count + existing_counts.get(id_val, 0)
                    if total_count > 1:
                        # Find all rows with this ID (using '_id' key in complete_row)
                        for row in rows_to_insert:
                            if row['_id'] == id_val:
                                duplicates.append({
                                    'order': row.get('ORDER', 'Unknown'),
                                    'id': id_val,
                                    'total_occurrences': total_count,
                                    'original_date': row.get('DATE', ''),
                                    'type': 'DUPLICATE'
                                })
                                self.logger.info(f"Found duplicate ID: {id_val} for Order: {row.get('ORDER', 'Unknown')} with {total_count} occurrences")

            # 7. Insert all rows, replacing existing IDs
            rows_inserted = 0
            rows_replaced = 0
            if rows_to_insert:
                try:
                    # --- CHANGE START ---
                    # Map actual headers to DB columns (ID -> _id)
                    db_columns = ['_id' if h == 'ID' else h for h in actual_headers]
                    # --- CHANGE END ---
                    
                    columns = ', '.join([f'`{h}`' for h in db_columns])
                    placeholders = ', '.join(['%s'] * len(db_columns))
                    insert_query = f"INSERT INTO `{table_name}` ({columns}) VALUES ({placeholders})"
                    
                    # Batch insert new rows
                    # We must extract values using the new 'db_columns' names which match 'complete_row' keys
                    batch_values = [[complete_row[col] for col in db_columns] for complete_row in rows_to_insert]
                    cursor.executemany(insert_query, batch_values)
                    rows_inserted = cursor.rowcount
                    self.connection.commit()
                    self.logger.info(f"Inserted {rows_inserted} rows into {table_name}, replaced {rows_replaced} duplicates")
                
                except Exception as e:
                    self.logger.error(f"Batch insert failed for {csv_file_path}: {str(e)}")
                    self.connection.rollback()
                    return False

            # 8. Send duplicate notification if any duplicates were found
            if duplicates:
                try:
                    self.email_notifier.notify_duplicate(table_name, duplicates, 'ID')
                    self.logger.info(f"Sent duplicate notification for {len(duplicates)} IDs")
                except Exception as e:
                    self.logger.error(f"Failed to send duplicate notification: {str(e)}")
                    for dup in duplicates:
                        self.logger.warning(f"Duplicate not notified: ID={dup['id']}, Order={dup['order']}, Total={dup['total_occurrences']}")

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
        """Create table with exact header names (expects headers list with '_id' instead of 'ID')"""
        cursor = None
        try:
            cursor = self.connection.cursor()
            
            # We add the auto-increment 'id' separately at the start
            columns = ["id INT NOT NULL AUTO_INCREMENT PRIMARY KEY"]
            
            for header in headers:
                # header is expected to be like '_id' now, not 'ID'
                sql_type = 'TEXT NOT NULL DEFAULT ""'
                columns.append(f"`{header}` {sql_type}")
            
            create_sql = f"CREATE TABLE `{table_name}` ({', '.join(columns)})"
            self.logger.debug(f"Executing CREATE TABLE query: {create_sql}")
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
        """Check if table exists in database"""
        try:
            cursor.execute(f"SHOW TABLES LIKE '{table_name}'")
            exists = cursor.fetchone() is not None
            self.logger.debug(f"Table '{table_name}' exists: {exists}")
            return exists
        except Exception as e:
            self.logger.error(f"Error checking table existence for '{table_name}': {str(e)}")
            return False