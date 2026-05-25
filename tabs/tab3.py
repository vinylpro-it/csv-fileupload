import tkinter as tk
from tkinter import ttk, messagebox
from typing import List, Dict, Optional
import re
from pathlib import Path
import json
from services.logger import Logger

class Tab3(ttk.Frame):
    def __init__(self, parent, config_manager, email_notifier=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.logger = Logger("Tab3")
        self.email_notifier = email_notifier
        self.email_settings = []
        # Define dynamic list of tables
        self.TABLES = [
            'glass', 'frame', 'rush', 'casingcutting', 'optlabel', 'casing',
            'extention', 'urbancutting', 'wrapping', 'extentioncutting', 'mullioncutting',
            'screencutting', 'stopcutting', 'glassstop_log', 'casing_log', 'screen_log',
            'extension_log'
        ]
        self.setup_ui()
        self.load_emails()

    def setup_ui(self):
        """Setup all UI components"""
        self.create_title()
        self.create_controls()
        self.create_email_table()

    def create_title(self):
        """Create title label"""
        ttk.Label(
            self, 
            text="Email Management", 
            font=('Tahoma', 12, 'bold')
        ).pack(pady=10)

    def create_controls(self):
        """Create control buttons"""
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill='x', padx=10, pady=5)
        
        ttk.Button(
            btn_frame, 
            text="Add New Email", 
            command=self.show_add_email_dialog
        ).pack(side='left', padx=5)
        
        ttk.Button(
            btn_frame,
            text="Refresh",
            command=self.refresh_emails
        ).pack(side='left', padx=5)

    def create_email_table(self):
        """Create and configure the email table with only Email and Actions"""
        columns = ("Email", "Actions")
        self.tree = ttk.Treeview(
            self, 
            columns=columns, 
            show="headings",
            selectmode="browse",
            height=15
        )
        
        # Configure columns
        col_widths = {
            "Email": 300,
            "Actions": 120
        }
        
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=col_widths[col], anchor='center')
        
        # Add scrollbar
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Bind events
        self.tree.bind("<Double-1>", self.on_edit_email)
        self.tree.bind("<Delete>", self.on_delete_email)
        
        # Configure tags for action buttons
        self.tree.tag_bind("edit_btn", "<Button-1>", self.on_edit_action)
        self.tree.tag_bind("delete_btn", "<Button-1>", self.on_delete_action)

    def load_emails(self):
        """Load emails from settings with error handling"""
        try:
            emails_data = self.config_manager.get_setting('emails', 'emails', '[]')
            if isinstance(emails_data, str):
                try:
                    emails_data = emails_data.strip()
                    if emails_data.startswith('[') and emails_data.endswith(']'):
                        self.email_settings = json.loads(emails_data)
                    else:
                        self.email_settings = []
                except json.JSONDecodeError as e:
                    self.logger.error(f"Invalid email JSON format: {str(e)}")
                    self.email_settings = []
            else:
                self.email_settings = emails_data if isinstance(emails_data, list) else []
            
            # Validate and normalize email settings
            valid_emails = []
            for email in self.email_settings:
                if isinstance(email, dict) and 'email' in email:
                    normalized_email = {
                        'email': email['email'].strip().lower()
                    }
                    # Initialize all tables dynamically
                    for table in self.TABLES:
                        normalized_email[table] = bool(email.get(table, False))
                    valid_emails.append(normalized_email)
            self.email_settings = valid_emails
            self.logger.info(f"Loaded {len(self.email_settings)} valid email configurations")
            self.refresh_email_table()
            
        except Exception as e:
            self.logger.error(f"Failed to load email settings: {str(e)}")
            self.email_settings = []

    def refresh_emails(self):
        """Reload emails from config"""
        self.load_emails()
        self.refresh_email_table()
        messagebox.showinfo("Refreshed", "Email list has been refreshed")

    def refresh_email_table(self):
        """Refresh the email table view"""
        self.tree.delete(*self.tree.get_children())
        
        for email_data in self.email_settings:
            self.tree.insert(
                "", 
                "end", 
                values=(
                    email_data["email"],
                    "Edit | Delete"
                ),
                tags=(email_data["email"], "edit_btn", "delete_btn")
            )

    def validate_email(self, email):
        """Validate email format with case-insensitive check"""
        if not isinstance(email, str):
            return False
        email = email.strip().lower()
        pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
        return re.match(pattern, email) is not None

    def is_email_duplicate(self, email):
        """Check for duplicate emails (case-insensitive)"""
        if not isinstance(email, str):
            return True
        email = email.strip().lower()
        return any(e["email"].lower() == email for e in self.email_settings)

    def show_add_email_dialog(self):
        """Show dialog to add new email"""
        dialog = tk.Toplevel(self)
        dialog.title("Add New Email")
        dialog.resizable(False, False)
        
        # Email Entry
        ttk.Label(dialog, text="Email Address:").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        email_entry = ttk.Entry(dialog, width=30)
        email_entry.grid(row=0, column=1, padx=5, pady=5)
        email_entry.focus_set()
        
        # Notification Options
        ttk.Label(dialog, text="Notification Tables:").grid(row=1, column=0, padx=5, pady=5, sticky="ne")
        
        options_frame = ttk.Frame(dialog)
        options_frame.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        
        # Dynamically create checkboxes for each table
        table_vars = {table: tk.BooleanVar(value=False) for table in self.TABLES}
        for table in self.TABLES:
            ttk.Checkbutton(options_frame, text=table.capitalize(), variable=table_vars[table]).pack(anchor='w')

        # Buttons
        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=10)
        
        ttk.Button(
            btn_frame, 
            text="Save", 
            command=lambda: self.save_email(
                email_entry.get(),
                {table: var.get() for table, var in table_vars.items()},
                dialog
            )
        ).pack(side='left', padx=5)
        
        ttk.Button(
            btn_frame, 
            text="Cancel", 
            command=dialog.destroy
        ).pack(side='left', padx=5)

    def save_email(self, email: str, table_settings: Dict[str, bool], dialog: tk.Toplevel):
        """Save new email to config"""
        if not self.validate_email(email):
            messagebox.showerror("Error", "Please enter a valid email address")
            return
        
        if self.is_email_duplicate(email):
            messagebox.showerror("Error", "This email already exists")
            return
        
        new_email = {
            "email": email.strip().lower()
        }
        # Add all table settings
        for table in self.TABLES:
            new_email[table] = table_settings.get(table, False)
        
        self.email_settings.append(new_email)
        self.save_emails_to_config()
        self.refresh_email_table()
        
        messagebox.showinfo("Success", "Email added successfully")
        dialog.destroy()

    def on_edit_email(self, event=None):
        """Handle email edit request"""
        selected = self.tree.selection()
        if not selected:
            return
        email = self.tree.item(selected[0], "values")[0]
        self.edit_email_dialog(email)

    def edit_email_dialog(self, email: str):
        """Show dialog to edit existing email"""
        email_data = next((e for e in self.email_settings if e["email"] == email), None)
        if not email_data:
            return
        
        dialog = tk.Toplevel(self)
        dialog.title("Edit Email")
        dialog.resizable(False, False)
        
        # Email Display (read-only)
        ttk.Label(dialog, text="Email Address:").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        ttk.Label(dialog, text=email_data["email"]).grid(row=0, column=1, padx=5, pady=5, sticky="w")
        
        # Notification Options
        ttk.Label(dialog, text="Notification Tables:").grid(row=1, column=0, padx=5, pady=5, sticky="ne")
        
        options_frame = ttk.Frame(dialog)
        options_frame.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        
        # Dynamically create checkboxes for each table
        table_vars = {table: tk.BooleanVar(value=email_data.get(table, False)) for table in self.TABLES}
        for table in self.TABLES:
            ttk.Checkbutton(options_frame, text=table.capitalize(), variable=table_vars[table]).pack(anchor='w')
        
        # Buttons
        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=10)
        
        ttk.Button(
            btn_frame, 
            text="Update", 
            command=lambda: self.update_email(
                email_data,
                {table: var.get() for table, var in table_vars.items()},
                dialog
            )
        ).pack(side='left', padx=5)
        
        ttk.Button(
            btn_frame, 
            text="Delete", 
            command=lambda: self.delete_email(email_data, dialog)
        ).pack(side='left', padx=5)
        
        ttk.Button(
            btn_frame, 
            text="Cancel", 
            command=dialog.destroy
        ).pack(side='left', padx=5)

    def update_email(self, email_data: Dict, table_settings: Dict[str, bool], dialog: tk.Toplevel):
        """Update existing email in config"""
        email_data.update({
            table: table_settings.get(table, False) for table in self.TABLES
        })
        
        self.save_emails_to_config()
        self.refresh_email_table()
        
        messagebox.showinfo("Success", "Email updated successfully")
        dialog.destroy()

    def on_delete_email(self, event=None):
        """Handle delete email request from keyboard"""
        selected = self.tree.selection()
        if not selected:
            return
        email = self.tree.item(selected[0], "values")[0]
        email_data = next((e for e in self.email_settings if e["email"] == email), None)
        if email_data:
            self.delete_email(email_data)

    def on_edit_action(self, event):
        """Handle edit button click in actions column"""
        item = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        
        if item and col == "#2":  # Actions column
            values = self.tree.item(item, "values")
            if "Edit" in values[1]:
                self.on_edit_email()

    def on_delete_action(self, event):
        """Handle delete button click in actions column"""
        item = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        
        if item and col == "#2":  # Actions column
            values = self.tree.item(item, "values")
            if "Delete" in values[1]:
                email = values[0]
                email_data = next((e for e in self.email_settings if e["email"] == email), None)
                if email_data:
                    self.delete_email(email_data)

    def delete_email(self, email_data: Dict, dialog: Optional[tk.Toplevel] = None):
        """Delete email from config"""
        if not messagebox.askyesno(
            "Confirm Delete",
            f"Are you sure you want to delete {email_data['email']}?"
        ):
            return
        
        self.email_settings = [e for e in self.email_settings if e["email"] != email_data["email"]]
        self.save_emails_to_config()
        self.refresh_email_table()
        
        messagebox.showinfo("Success", "Email deleted successfully")
        if dialog:
            dialog.destroy()

    def save_emails_to_config(self):
        """Save emails to config with proper JSON serialization"""
        try:
            email_data = [{
                "email": str(email["email"]).strip().lower(),
                **{table: bool(email.get(table, False)) for table in self.TABLES}
            } for email in self.email_settings]
            
            self.config_manager.update_setting("emails", "emails", json.dumps(email_data, indent=2))
            
        except Exception as e:
            messagebox.showerror("Save Error", f"Failed to save emails: {str(e)}")
            raise

    def test_email_config(self):
        """Test email configuration"""
        if not self.email_notifier or not self.email_settings:
            messagebox.showwarning("Cannot Test", "No email notifier configured or no emails available")
            return
        
        # Find an email with at least one table enabled
        test_email = next(
            (e["email"] for e in self.email_settings if any(e.get(table, False) for table in self.TABLES)),
            None
        )
        
        if not test_email:
            messagebox.showwarning("Cannot Test", "No enabled emails found for testing")
            return
        
        try:
            self.email_notifier.notify_duplicate(
                "test_table",
                [{"order": "TEST-123", "barcode": "TEST-BC123"}],
                "barcode"
            )
            messagebox.showinfo(
                "Test Sent",
                f"Test email sent to {test_email}\n"
                "Please check the recipient's inbox (and spam folder)"
            )
        except Exception as e:
            messagebox.showerror(
                "Test Failed",
                f"Failed to send test email: {str(e)}"
            )