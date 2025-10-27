from processors.base_processor import BaseProcessor
from pathlib import Path
from config.config import ConfigManager
import mysql.connector
from mysql.connector import Error
import csv
import os
import shutil
import tempfile
import re

class PAYMENTProcessor(BaseProcessor):
    """
    Processor for PAYMENT CSV files.
    - Ensures headers
    - Creates table if missing
    - Upserts rows by UNIQUE_KEY ('Order#')
    - Normalizes currency columns to DECIMAL-compatible strings
    - Moves processed files to move_dir on success
    """

    # ======== CONFIG ========
    TABLE_NAME = "payment"                  # destination table name
    UNIQUE_KEY = "Order#"                   # CSV unique identifier
    EXPECTED_HEADERS = ['Order#', 'Acct', 'Dealer name', 'Customer po', 'Surcharge', 'Sub total', 'Taxes', 'Deposit', 'Amount Due']            # Expected header order
    PROTECTED_COLUMNS = []                  # e.g., ["CREATED_AT"]
    READ_ENCODINGS = ["utf-8", "utf-8-sig", "cp1256", "cp1252", "latin-1"]

    # SQL types for explicit schema
    COLUMN_TYPES = {
        "id": "INT NOT NULL AUTO_INCREMENT PRIMARY KEY",
        "Order#": "VARCHAR(64) NOT NULL",
        "Acct": "VARCHAR(64) NOT NULL",
        "Dealer name": "VARCHAR(255) NOT NULL",
        "Customer po": "VARCHAR(255) NOT NULL",
        "Surcharge": "DECIMAL(12,2) NOT NULL DEFAULT 0.00",
        "Sub total": "DECIMAL(12,2) NOT NULL DEFAULT 0.00",
        "Taxes": "DECIMAL(12,2) NOT NULL DEFAULT 0.00",
        "Deposit": "DECIMAL(12,2) NOT NULL DEFAULT 0.00",
        "Amount Due": "DECIMAL(12,2) NOT NULL DEFAULT 0.00"
    }
    # ======== END CONFIG ========

    CURRENCY_COLUMNS = ['Surcharge', 'Sub total', 'Taxes', 'Deposit', 'Amount Due']

    def __init__(self, db_handler, email_notifier, logger):
        super().__init__(db_handler, email_notifier, logger)
        self.config_manager = ConfigManager()
        self.connection = None
        self.config = None

    def get_table_name(self):
        return self.TABLE_NAME

    # ---------- DB CONNECTION ----------
    def connect(self):
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
        if self.connection and self.connection.is_connected():
            self.connection.close()
            self.logger.info("Database connection closed")

    # ---------- MAIN ENTRY ----------
    def process(self, file_path: Path, move_dir: Path) -> bool:
        try:
            self.logger.info(f"Processing {self.TABLE_NAME} file: {file_path}")
            if not self.connect():
                return False

            success = self.upload_csv_data(self.TABLE_NAME, str(file_path))

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
            self.logger.error(f"Error processing {self.TABLE_NAME} file {file_path}: {str(e)}")
            return False
        finally:
            self.disconnect()

    # ---------- HELPERS ----------
    _money_cleaner = re.compile(r"[^0-9\-\.]")

    def _normalize_currency(self, value: str) -> str:
        if value is None:
            return "0.00"
        v = value.strip()
        if v == "":
            return "0.00"
        # Remove currency symbols and thousands separators
        v = self._money_cleaner.sub("", v)
        try:
            # Coerce to two-decimal string
            return f"{float(v):.2f}"
        except Exception:
            self.logger.warning(f"Could not parse currency value '{value}', defaulting to 0.00")
            return "0.00"

    # ---------- CSV / UPSERT ----------
    def upload_csv_data(self, table_name, csv_file_path):
        if not self.connection or not self.connection.is_connected():
            if not self.connect():
                return False

        cursor = None
        try:
            # 1) Ensure headers exist & order
            if self.EXPECTED_HEADERS:
                with open(csv_file_path, 'r', encoding='utf-8', errors='ignore') as raw:
                    first_line = raw.readline().strip()
                    first_line_headers = [h.strip() for h in first_line.split(',')]
                    has_expected_headers = (first_line_headers == self.EXPECTED_HEADERS)

                if not has_expected_headers:
                    temp_path = os.path.join(tempfile.gettempdir(), os.path.basename(csv_file_path) + ".tmp")
                    try:
                        with open(temp_path, 'w', newline='', encoding='utf-8') as temp_file, \
                             open(csv_file_path, 'r', encoding='utf-8', errors='ignore') as original:
                            temp_file.write(','.join(self.EXPECTED_HEADERS) + '\n')
                            temp_file.write(first_line + '\n')
                            temp_file.writelines(original.readlines())
                        shutil.move(temp_path, csv_file_path)
                        self.logger.warning(f"Added headers to CSV file for {table_name}: {self.EXPECTED_HEADERS}")
                    except Exception as e:
                        self.logger.error(f"Error adding headers to {csv_file_path}: {str(e)}")
                        return False

            # 2) Open with an encoding that works
            csvfile = None
            last_err = None
            for enc in self.READ_ENCODINGS:
                try:
                    csvfile = open(csv_file_path, 'r', encoding=enc, errors='ignore', newline='')
                    csvreader = csv.DictReader(csvfile)
                    actual_headers = [h.strip() for h in csvreader.fieldnames]
                    break
                except Exception as e:
                    last_err = e
                    if csvfile:
                        csvfile.close()
                    csvfile = None
                    continue
            if not csvfile:
                self.logger.error(f"Failed to open CSV with encodings {self.READ_ENCODINGS}: {last_err}")
                return False

            with csvfile:
                self.logger.info(f"Processing CSV with columns: {actual_headers}")

                # 3) Ensure table exists
                cursor = self.connection.cursor(buffered=True)
                cursor.execute(f"SHOW TABLES LIKE '{table_name}'")
                if not cursor.fetchone():
                    if not self._create_table(table_name, actual_headers):
                        return False

                db_columns = [h for h in actual_headers]
                if self.UNIQUE_KEY not in db_columns:
                    self.logger.error(f"UNIQUE_KEY '{self.UNIQUE_KEY}' not present in CSV headers: {db_columns}")
                    return False

                rows_inserted = rows_updated = 0

                # 4) Iterate rows
                for row in csvreader:
                    try:
                        complete_row = {h: (row.get(h, '') or '') for h in actual_headers}
                        unique_value = complete_row.get(self.UNIQUE_KEY, '').strip()

                        # Normalize values
                        for header in actual_headers:
                            if header in self.CURRENCY_COLUMNS:
                                complete_row[header] = self._normalize_currency(complete_row[header])
                            else:
                                if header not in self.PROTECTED_COLUMNS:
                                    val = complete_row[header]
                                    if val is not None:
                                        val = val.replace('°', 'deg').strip()
                                        complete_row[header] = val

                        if not unique_value:
                            self.logger.warning(f"Skipping row with missing {self.UNIQUE_KEY}: {complete_row}")
                            continue

                        # Check existence
                        query = f"SELECT {', '.join([f'`{col}`' for col in db_columns])} FROM `{table_name}` WHERE `{self.UNIQUE_KEY}` = %s"
                        cursor.execute(query, (unique_value,))
                        existing_row = cursor.fetchone()

                        if existing_row:
                            update_columns, update_values = [], []
                            for col in db_columns:
                                if col in self.PROTECTED_COLUMNS:
                                    continue
                                new_value = complete_row[col]
                                if new_value != '':
                                    update_columns.append(col)
                                    update_values.append(new_value)

                            if update_columns:
                                set_clause = ', '.join([f"`{col}` = %s" for col in update_columns])
                                update_query = f"UPDATE `{table_name}` SET {set_clause} WHERE `{self.UNIQUE_KEY}` = %s"
                                values = update_values + [unique_value]
                                cursor.execute(update_query, values)
                                rows_updated += 1
                                self.logger.info(f"Updated {self.UNIQUE_KEY}={unique_value} with {len(update_columns)} columns")
                            else:
                                self.logger.info(f"No updates for {self.UNIQUE_KEY}={unique_value} (all empty)")
                        else:
                            # Insert new row
                            columns = ', '.join([f'`{h}`' for h in db_columns])
                            placeholders = ', '.join(['%s'] * len(db_columns))
                            insert_query = f"INSERT INTO `{table_name}` ({columns}) VALUES ({placeholders})"
                            values = [complete_row[h] if h not in self.CURRENCY_COLUMNS else self._normalize_currency(complete_row[h]) for h in db_columns]
                            cursor.execute(insert_query, values)
                            rows_inserted += 1
                            self.logger.info(f"Inserted new row {self.UNIQUE_KEY}={unique_value}")

                    except Exception as e:
                        self.logger.error(f"Row processing error for {self.UNIQUE_KEY}={unique_value}: {str(e)}")
                        continue

                self.connection.commit()
                self.logger.info(f"Inserted {rows_inserted}, updated {rows_updated} into {table_name}")
                return True

        except Exception as e:
            self.logger.error(f"Upload failed for {csv_file_path}: {str(e)}")
            if self.connection:
                self.connection.rollback()
            return False
        finally:
            if cursor:
                cursor.close()

    # ---------- DDL ----------
    def _create_table(self, table_name, headers):
        cursor = None
        try:
            cursor = self.connection.cursor(buffered=True)

            # Column definitions
            columns_sql = ["id " + self.COLUMN_TYPES.get("id", "INT NOT NULL AUTO_INCREMENT PRIMARY KEY")]
            for header in headers:
                if header == "id":
                    continue
                sql_type = self.COLUMN_TYPES.get(header, 'TEXT NOT NULL DEFAULT ""')
                columns_sql.append(f"`{header}` {sql_type}")

            create_sql = f"CREATE TABLE `{table_name}` ({', '.join(columns_sql)})"
            cursor.execute(create_sql)
            self.connection.commit()
            self.logger.info(f"Created table `{table_name}` with {len(headers)} columns (+ id)")
            return True
        except Exception as e:
            self.logger.error(f"Failed to create table '{table_name}': {str(e)}")
            return False
        finally:
            if cursor:
                cursor.close()
