from processors.base_processor import BaseProcessor
from pathlib import Path
from config.config import ConfigManager
import mysql.connector
from mysql.connector import Error
import csv
import os
import shutil
import tempfile

class CASING_LOGProcessor(BaseProcessor):
    def __init__(self, db_handler, email_notifier, logger):
        super().__init__(db_handler, email_notifier, logger)
        self.config_manager = ConfigManager()
        self.connection = None

    def get_table_name(self):
        return "casing_log"

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
            self.logger.info(f"Processing casing_log file: {file_path}")
            
            if not self.connect():
                return False

            success = self.upload_csv_data(self.get_table_name(), str(file_path))
            
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
            self.logger.error(f"Error processing casing_log file {file_path}: {str(e)}")
            return False
        finally:
            self.disconnect()

    def upload_csv_data(self, table_name, csv_file_path):
        """Upload CSV data to the screen_log table, checking for resends before inserts"""
        if not self.connection or not self.connection.is_connected():
            if not self.connect():
                return False
        
        cursor = None
        try:
            # 1. Define expected headers (from CSV file)
            csv_headers = [
                'SIZE', 'H AND W', 'BIN', 'LINE NUMBER', 'PROFILE TYPE',
                'LABEL', 'ORDER NUMBER', 'WINDOW_TYPE' , 'WINDOW SIZE', 'WINDOW LINE',
                'OT', 'COLOUR IN', 'COLOUR OUT', 'RUBBER COLOUR', 'COMPANY NAME',
                'CUSTOMER PO', 'ID','DATE', 'TIME', 'CART'
            ]

            # 2. Check if CSV file has the expected headers
            has_expected_headers = False
            first_line_headers = []
            with open(csv_file_path, 'r', encoding='utf-8') as csvfile:
                first_line = csvfile.readline().strip()
                first_line_headers = [h.strip() for h in first_line.split(',')]
                normalized_first_line_headers = [h.lower().strip() for h in first_line_headers]
                normalized_expected_headers = [h.lower().strip() for h in csv_headers]
                has_expected_headers = normalized_first_line_headers == normalized_expected_headers
                self.logger.info(f"CSV headers: {first_line_headers}, Expected: {csv_headers}, Match: {has_expected_headers}")

            # If headers are missing, create a temporary file with headers
            if not has_expected_headers:
                temp_dir = tempfile.gettempdir()
                temp_path = os.path.join(temp_dir, os.path.basename(csv_file_path) + ".tmp")
                
                try:
                    with open(csv_file_path, 'r', encoding='utf-8') as infile, \
                        open(temp_path, 'w', newline='', encoding='utf-8') as outfile:
                        # Write expected headers (CSV standard)
                        outfile.write(','.join(csv_headers) + '\n')
                        # Copy all lines, assuming no headers in original
                        infile.seek(0)
                        outfile.writelines(infile.readlines())
                    
                    # Replace original file with temp file
                    shutil.move(temp_path, csv_file_path)
                    self.logger.warning(f"Added headers to CSV file: {csv_headers}")
                except Exception as e:
                    self.logger.error(f"Error adding headers to {csv_file_path}: {str(e)}")
                    return False
            
            # 3. Collect all rows and check for resends
            rows_to_insert = []
            order_IDs = set()  # Track unique ORDER values in the file
            resends = []
            
            with open(csv_file_path, 'r', encoding='utf-8') as csvfile:
                csvreader = csv.DictReader(csvfile)
                actual_headers = [h.strip() for h in csvreader.fieldnames]
                
                self.logger.info(f"Processing CSV with columns: {actual_headers}")

                # Check for duplicate header rows
                normalized_headers = [h.lower().strip() for h in csv_headers]
                for row in csvreader:
                    # Check if the row is a duplicate header
                    row_values = [str(row.get(h, '')).lower().strip() for h in actual_headers]
                    if row_values == normalized_headers:
                        self.logger.warning(f"Skipping duplicate header row: {row_values}")
                        continue
                    
                    try:
                        # Convert None or empty values to empty strings
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
                        # Remap 'ID' to '_ID' to avoid conflict with PK
                        if 'ID' in complete_row:
                            complete_row['_ID'] = complete_row.pop('ID')
                        # --- CHANGE END ---

                        order_ID = complete_row.get('_ID', '')

                        if not order_ID:
                            self.logger.warning(f"Skipping row with missing ORDER: {complete_row}")
                            continue

                        # Collect unique ORDER values
                        order_IDs.add(order_ID)
                        rows_to_insert.append(complete_row)

                    except Exception as e:
                        self.logger.error(f"Row processing error for row {complete_row}: {str(e)}")
                        continue

            # 4. Check for existing orders in the database
            cursor = self.connection.cursor()
            for order_ID in order_IDs:
                try:
                    query = f"SELECT `_ID`, `DATE` FROM `{table_name}` WHERE `_ID` = %s LIMIT 1"
                    cursor.execute(query, (order_ID,))
                    existing_order = cursor.fetchone()
                    
                    if existing_order:
                        resends.append({
                            'order': order_ID,
                            'original_date': existing_order[1] or ''
                        })
                        self.logger.info(f"Found existing ORDER for resend: {order_ID}")
                except Exception as e:
                    self.logger.error(f"Error checking ORDER {order_ID}: {str(e)}")
                    continue

            # 5. Create table if it doesn't exist
            # --- CHANGE START ---
            # Map actual CSV headers to DB columns (ID -> _ID)
            db_columns = ['_ID' if h == 'ID' else h for h in actual_headers]
            
            cursor.execute(f"SHOW TABLES LIKE '{table_name}'")
            if not cursor.fetchone():
                if not self._create_table(table_name, db_columns):
                    return False
            # --- CHANGE END ---

            # 6. Insert all rows in a batch
            rows_inserted = 0
            if rows_to_insert:
                try:
                    # db_columns is already prepared (ID is _ID)
                    columns = ', '.join([f'`{h}`' for h in db_columns])
                    placeholders = ', '.join(['%s'] * len(db_columns))
                    insert_query = f"INSERT INTO `{table_name}` ({columns}) VALUES ({placeholders})"
                    
                    for complete_row in rows_to_insert:
                        # Extract values using the mapped DB column names
                        values = [complete_row.get(h, '') for h in db_columns]
                        cursor.execute(insert_query, values)
                        rows_inserted += 1
                        self.logger.info(f"Inserted row for _ID: {complete_row.get('_ID', '')}")
                    
                    self.connection.commit()
                    self.logger.info(f"Inserted {rows_inserted} rows into {table_name}")
                
                except Exception as e:
                    self.logger.error(f"Batch insert failed for {csv_file_path}: {str(e)}")
                    self.connection.rollback()
                    return False

            # 7. Send resend notification if any resends were found
            if resends:
                try:
                    self.email_notifier.notify_resend(table_name, resends, 'ORDER')
                    self.logger.info(f"Sent resend notification for {len(resends)} orders")
                except Exception as e:
                    self.logger.error(f"Failed to send resend notification: {str(e)}")

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
        """Create table with appropriate structure (expects mapped headers, e.g. _ID instead of ID)"""
        cursor = None
        try:
            cursor = self.connection.cursor()
            
            # Build column definitions
            columns = []
            columns.append("id INT NOT NULL AUTO_INCREMENT PRIMARY KEY")  # Add ID column first
            
            for header in headers:
                # 'id' is already added as PK, so skip if passed in headers (though our mapping produces _ID)
                # But we add a check just in case
                if header.lower() != 'id': 
                    sql_type = 'TEXT NOT NULL DEFAULT ""'
                    columns.append(f"`{header}` {sql_type}")
            
            # Create the table
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
        cursor.execute(f"SHOW TABLES LIKE '{table_name}'")
        return cursor.fetchone() is not None