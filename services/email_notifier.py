import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from dotenv import load_dotenv
import os
import json
import configparser
from pathlib import Path
from services.logger import Logger
from utils.display_name import get_table_display_name

class EmailNotifier:
    def __init__(self, smtp_server=None, smtp_port=None, sender_email=None, sender_password=None):
        self.logger = Logger("EmailNotifier")
        load_dotenv()

        # خواندن تنظیمات SMTP از .env
        self.smtp_server = smtp_server or os.getenv('SMTP_SERVER')
        self.smtp_port = int(smtp_port or os.getenv('SMTP_PORT', 465))
        self.sender_email = sender_email or os.getenv('SENDER_EMAIL')
        self.sender_password = sender_password or os.getenv('SENDER_PASSWORD')

        # خواندن تنظیمات ایمیل‌ها از فایل settings.txt
        self.email_settings = self._load_email_settings()
        
        if not all([self.smtp_server, self.smtp_port, self.sender_email, self.sender_password]):
            self.logger.error("Incomplete SMTP configuration! Email notifications will be disabled.")
            self.enabled = False
        else:
            self.enabled = True

    def _load_email_settings(self):
        """بارگذاری تنظیمات ایمیل از فایل settings.ini"""
        try:
            # خواندن فایل تنظیمات
            config = configparser.ConfigParser()
            config.read(Path('config/settings.ini'))
            
            # بررسی وجود بخش emails
            if 'emails' in config and 'emails' in config['emails']:
                email_data = config['emails']['emails']
                
                # حذف فاصله‌های اضافی و خطوط جدید
                email_data = ' '.join(email_data.split())
                
                # تبدیل JSON به لیست
                try:
                    email_list = json.loads(email_data)
                    if not isinstance(email_list, list):
                        self.logger.warning("Email settings is not a list, converting to list")
                        email_list = [email_list]
                    return email_list
                except json.JSONDecodeError as e:
                    self.logger.error(f"Invalid JSON format in email settings: {str(e)}")
                    return []
            return []
        
        except Exception as e:
            self.logger.error(f"Failed to load email settings: {str(e)}")
            return []


    def notify_duplicate(self, table_name, duplicates, key_field):
        """Notify about duplicate orders (both order and sealed_unit_id match)"""
        if not self.enabled:
            return

        recipients = self.get_recipients_for_table(table_name)
        if not recipients:
            self.logger.info(f"No recipients configured for table: {table_name}")
            return

        table_display_name = get_table_display_name(table_name)
        
        subject = f"🔴 Alert! duplicate {table_display_name} Order {datetime.now().strftime('[%Y-%m-%d %I:%M %p]')}"
        
        # Deduplicate orders based on 'order' field
        unique_duplicates = {}
        for dup in duplicates:
            order = dup['order']
            if order not in unique_duplicates:
                unique_duplicates[order] = dup
        
        # Format unique duplicates for email body
        formatted_duplicates = []
        for dup in unique_duplicates.values():
            formatted_duplicates.append(
                f"Order: {dup['order']}, "
                f"Date: {dup['original_date']}"
            )
            
        body = f"""
        <html>
        <body>
            <h2>Duplicate {table_display_name} Orders Detected</h2>
            <ul>
                {"".join(f"<li>{item}</li>" for item in formatted_duplicates)}
            </ul>
        </body>
        </html>
        """

        self._send_email(recipients, subject, body)

    def notify_resend(self, table_name, resends, key_field):
        """Notify about re-sent orders (order number match only)"""
        if not self.enabled:
            return

        recipients = self.get_recipients_for_table(table_name)
        if not recipients:
            self.logger.info(f"No recipients configured for table: {table_name}")
            return

        table_display_name = get_table_display_name(table_name)
        
        subject = f"⚠️ Alert! re send {table_display_name} Order {datetime.now().strftime('[%Y-%m-%d %I:%M %p]')}"
        
        # Deduplicate resends based on 'order' field
        unique_resends = {}
        for resend in resends:
            order = resend['order']
            if order not in unique_resends:
                unique_resends[order] = resend
        
        # Format unique resends for email body
        formatted_resends = []
        for resend in unique_resends.values():
            formatted_resends.append(
                f"Order: {resend['order']}, Date: {resend['original_date']}"
            )
            
        body = f"""
        <html>
        <body>
            <h2>Re-Send {table_display_name} Orders Detected</h2>
            <ul>
                {"".join(f"<li>{item}</li>" for item in formatted_resends)}
            </ul>
        </body>
        </html>
        """

        self._send_email(recipients, subject, body)


    def notify_rush(self, table_name, rush_orders):
        """Notify about rush orders"""
        if not self.enabled:
            return

        recipients = self.get_recipients_for_table(table_name)
        if not recipients:
            self.logger.info(f"No recipients configured for table: {table_name}")
            return

        table_display_name = get_table_display_name(table_name)
        
        subject = f"⚠️ Alert! Rush orders send to cut {datetime.now().strftime('[%Y-%m-%d %I:%M:%S %p]')}"
        
        # Deduplicate rush orders based on 'order' field
        unique_rush_orders = {}
        for order in rush_orders:
            order_id = order['order']
            if order_id not in unique_rush_orders:
                unique_rush_orders[order_id] = order
        
        # Format unique rush orders for email body
        formatted_rush_orders = []
        for order in unique_rush_orders.values():
            formatted_rush_orders.append(
                f"Order: {order['order']}" #, Date: {order['list_date']}"
            )
            
        body = f"""
        <html>
        <body>
            <h2>Rush Orders for {table_display_name} Sent to Cut</h2>
            <ul>
                {"".join(f"<li>{item}</li>" for item in formatted_rush_orders)}
            </ul>
        </body>
        </html>
        """

        self._send_email(recipients, subject, body)

    def _send_email(self, recipients, subject, body):
        """Send email to recipients"""
        msg = MIMEMultipart('alternative')
        msg['From'] = f"vinylpro notification <{self.sender_email}>"
        msg['Subject'] = subject
        msg['X-Priority'] = '1'

        try:
            # روش امن‌تر با تشخیص خودکار پروتکل
            if self.smtp_port == 465:
                # استفاده از SSL
                with smtplib.SMTP_SSL(self.smtp_server, self.smtp_port) as server:
                    server.login(self.sender_email, self.sender_password)
                    self._send_emails(server, msg, recipients, body)
            else:
                # استفاده از STARTTLS
                with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                    server.starttls()  # فعال‌سازی TLS
                    server.login(self.sender_email, self.sender_password)
                    self._send_emails(server, msg, recipients, body)
                    
        except Exception as e:
            self.logger.error(f"SMTP connection failed: {str(e)}")
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}")

    def _send_emails(self, server, msg, recipients, body):
        """Send emails to list of recipients"""
        for recipient in recipients:
            try:
                recipient_msg = MIMEMultipart('alternative')
                recipient_msg['From'] = msg['From']
                recipient_msg['Subject'] = msg['Subject']
                recipient_msg['X-Priority'] = msg['X-Priority']
                recipient_msg['To'] = recipient
                
                html_part = MIMEText(body, 'html')
                recipient_msg.attach(html_part)
                
                server.send_message(recipient_msg)
                self.logger.info(f"Notification sent to {recipient}")
            except Exception as e:
                self.logger.error(f"Failed to send email to {recipient}: {str(e)}")

    def get_recipients_for_table(self, table_name):
        """Get email recipients for specific table type"""
        recipients = []
        table_type = table_name
        if not table_type:
            self.logger.warning(f"No table type matched for: {table_name}")
            return recipients
        
        self.logger.debug(f"Checking emails for table type: {table_type}")
        
        for email_config in self.email_settings:
            if email_config.get(table_type, False):
                recipients.append(email_config['email'])
                self.logger.debug(f"Added recipient: {email_config['email']} for {table_type}")
        
        if not recipients:
            self.logger.info(f"No recipients configured for table: {table_name} (type: {table_type})")
        else:
            self.logger.info(f"Found {len(recipients)} recipients for {table_name}")
        
        return recipients

    def _determine_table_type(self, table_name):
        """Determine table type based on name"""
        table_name = table_name.lower().strip()
        
        patterns = {
            'frame': ['frame', 'framereport', 'framescutting'],
            'glass': ['glass', 'glassreport', 'glazing'],
            'rush': ['rush', 'urgent'],
            'casingcutting': ['casingcutting'],
            'optlabel': ['optlabel'],
            'casing': ['casing'],
            'extention': ['extention'],
            'urbancutting': ['urbancutting'],
            'wrapping': ['wrapping'],
            'extentioncutting': ['extentioncutting'],
            'mullioncutting': ['mullioncutting'],
            'screencutting':['screencutting'],
            'stopcutting': ['stopcutting']
        }
        
        for table_type, keywords in patterns.items():
            if any(keyword in table_name for keyword in keywords):
                return table_type
        
        return None