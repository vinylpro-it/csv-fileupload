from processors.base_processor import BaseProcessor
from pathlib import Path
from config.config import ConfigManager
import mysql.connector
from mysql.connector import Error
import csv
import os
import shutil

class WORKORDERProcessor(BaseProcessor):
    def __init__(self, db_handler, email_notifier, logger):
        super().__init__(db_handler, email_notifier, logger)
        self.config_manager = ConfigManager()
        self.connection = None

    def get_table_name(self):
        return "workorder"

    def connect(self):
        """Establish database connection"""
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
            self.logger.error(f"Error connecting to MySQL: {str(e)}")
            return False

    def disconnect(self):
        """Close database connection"""
        if self.connection and self.connection.is_connected():
            self.connection.close()
            self.logger.info("Database connection closed")

    def process(self, file_path: Path, move_dir: Path) -> bool:
        try:
            self.logger.info(f"Processing WORKORDER file: {file_path}")
            
            if not self.connect():
                return False

            success = self.upload_csv_data(self.get_table_name(), str(file_path), self.email_notifier)
            
            if success:
                # Move file to move_dir after successful processing
                try:
                    destination = move_dir / file_path.name
                    shutil.move(str(file_path), str(destination))
                    self.logger.info(f"Moved file to {destination}")
                except Exception as e:
                    self.logger.error(f"Failed to move file {file_path} to {move_dir}: {str(e)}")
                    return False
            
            return success
            
        except Exception as e:
            self.logger.error(f"Error processing WORKORDER file {file_path}: {str(e)}")
            return False
        finally:
            self.disconnect()

    def upload_csv_data(self, table_name, csv_file_path, email_notifier=None):
        """Upload CSV data with robust delimiter detection and proper email notification"""
        if not self.connection or not self.connection.is_connected():
            if not self.connect():
                return False
        
        cursor = None
        try:
            # 1. Define expected headers
            headers = [
                'ORDER #', 'PO', 'TAG', 'DEALER', 'ORDER DATE', 'DUE DATE', 'WINDOW DESCRIPTION',
                'DESCRIPTION', 'OPTIONS', 'QTY', 'LINE #1', 'NOTE'
            ]

            # 2. Check if CSV file already has the expected headers
            # FIX: Changed encoding to 'Windows-1252' to prevent crash on readlines()
            with open(csv_file_path, 'r', encoding='Windows-1252') as csvfile:
                first_line = csvfile.readline().strip()
                first_line_headers = [h.strip() for h in first_line.split(',')]
                has_expected_headers = first_line_headers == headers
                
                if not has_expected_headers:
                    # Create temp file with headers
                    import tempfile
                    temp_dir = tempfile.gettempdir()
                    temp_path = os.path.join(temp_dir, os.path.basename(csv_file_path) + ".tmp")
                    
                    try:
                        # We write the temp file as UTF-8 (standard)
                        with open(temp_path, 'w', newline='', encoding='utf-8') as temp_file:
                            temp_file.write(','.join(headers) + '\n')
                            temp_file.write(first_line + '\n')  # Write the first line as data
                            temp_file.writelines(csvfile.readlines())  # Write the rest
                        
                        # Replace original file with temp file
                        shutil.move(temp_path, csv_file_path)
                        self.logger.warning(f"Added headers to CSV file: {headers}")
                    except Exception as e:
                        self.logger.error(f"Error adding headers to {csv_file_path}: {str(e)}")
                        return False
            
            # 3. Read all rows and process duplicates
            rows_to_insert = []
            updated_rows = 0
            new_rows = 0
            
            # FIX: Read with 'Windows-1252' to match the source file format
            with open(csv_file_path, 'r', encoding='Windows-1252') as csvfile:
                csvreader = csv.DictReader(csvfile)
                actual_headers = [h.strip() for h in csvreader.fieldnames]
                
                self.logger.info(f"Processing CSV with columns: {actual_headers}")

                # Check/create table
                cursor = self.connection.cursor()
                cursor.execute(f"SHOW TABLES LIKE '{table_name}'")
                if not cursor.fetchone():
                    if not self._create_table(table_name, actual_headers):
                        return False

                # Exclude 'OPTIONS' from database columns
                db_columns = [h for h in actual_headers if h != 'OPTIONS']
                
                # Collect all rows and handle duplicates
                for row in csvreader:
                    try:
                        # Convert None or empty values to empty strings
                        complete_row = {h: row.get(h, '') or '' for h in actual_headers}
                        
                        # Trim spaces and clean special chars for all columns
                        for header in actual_headers:
                            value = complete_row[header]
                            if value is not None:
                                # FIX: Replace degree symbol (or other special chars) like in ORDERSUMMARYProcessor
                                if isinstance(value, str):
                                    value = value.replace('°', 'deg')
                                
                                # If the value is all whitespace, set to empty string
                                if value.strip() == '':
                                    complete_row[header] = ''
                                # Otherwise, trim leading and trailing spaces
                                elif value != value.strip():
                                    complete_row[header] = value.strip()

                        order_id = complete_row.get('ORDER #', '')

                        # Combine DESCRIPTION and OPTIONS
                        description = complete_row.get('DESCRIPTION', '')
                        options = complete_row.get('OPTIONS', '')
                        complete_row['DESCRIPTION'] = f"{description}##{options}" if options else description

                        if not order_id:
                            self.logger.warning(f"Skipping row with missing ORDER #: {complete_row}")
                            continue

                        # Check for duplicates
                        query = """
                        SELECT * 
                        FROM `workorder` 
                        WHERE `ORDER #` = %s 
                        """
                        cursor.execute(query, (order_id,))  # Pass order_id as a tuple
                        result = cursor.fetchall()
                        
                        if result:
                            # Delete existing rows with this ORDER #
                            delete_query = """
                            DELETE FROM `workorder` 
                            WHERE `ORDER #` = %s 
                            """
                            cursor.execute(delete_query, (order_id,))
                            self.logger.info(f"Deleted {len(result)} existing row(s) for ORDER #: {order_id}")
                            updated_rows += 1
                            rows_to_insert.append(complete_row)
                        else:
                            new_rows += 1
                            rows_to_insert.append(complete_row)

                    except Exception as e:
                        self.logger.error(f"Row processing error for row {complete_row}: {str(e)}")
                        continue

            # 4. Insert all rows in a single batch
            if rows_to_insert:
                try:
                    columns = ', '.join([f'`{h}`' for h in db_columns])
                    placeholders = ', '.join(['%s'] * len(db_columns))
                    insert_query = f"INSERT INTO `{table_name}` ({columns}) VALUES ({placeholders})"
                    
                    # Batch insert all rows
                    for complete_row in rows_to_insert:
                        values = [complete_row[h] for h in db_columns]
                        cursor.execute(insert_query, values)
                    
                    self.connection.commit()
                    self.logger.info(f"Inserted {new_rows} new rows, updated {updated_rows} rows")
                
                except Exception as e:
                    self.logger.error(f"Batch insert failed for {csv_file_path}: {str(e)}")
                    self.connection.rollback()
                    return False
            
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
        """Create table with appropriate structure"""
        cursor = None
        try:
            cursor = self.connection.cursor()
            
            # Map Python types to SQL types, excluding OPTIONS
            type_mapping = {
                'ORDER #': 'TEXT NOT NULL DEFAULT ""',
                'PO': 'TEXT NOT NULL DEFAULT ""',
                'TAG': 'TEXT NOT NULL DEFAULT ""',
                'DEALER': 'TEXT NOT NULL DEFAULT ""',
                'ORDER DATE': 'TEXT NOT NULL DEFAULT ""',
                'DUE DATE': 'TEXT NOT NULL DEFAULT ""',
                'WINDOW DESCRIPTION': 'TEXT NOT NULL DEFAULT ""',
                'DESCRIPTION': 'TEXT NOT NULL DEFAULT ""',
                'QTY': 'TEXT NOT NULL DEFAULT ""',
                'LINE #1': 'TEXT NOT NULL DEFAULT ""',
                'NOTE': 'TEXT NOT NULL DEFAULT ""'
            }
            
            # Build column definitions, excluding OPTIONS
            columns = ["id INT NOT NULL AUTO_INCREMENT"]  # بدون PRIMARY KEY اینجا
            
            for header in headers:
                if header in type_mapping and header != 'OPTIONS':
                    sql_type = type_mapping[header]
                    columns.append(f"`{header}` {sql_type}")

            # Add PRIMARY KEY constraint separately
            columns.append("PRIMARY KEY (`id`)")
            
            # Create the table
            create_sql = f"CREATE TABLE `{table_name}` ({', '.join(columns)})"
            cursor.execute(create_sql)
            
            self.connection.commit()
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to create table '{table_name}': {str(e)}")
            return False
        finally:
            if cursor:
                cursor.close()

    def _table_exists(self, cursor, table_name):
        """Check if table exists in database"""
        cursor.execute(f"SHOW TABLES LIKE '{table_name}'")
        return cursor.fetchone() is not None