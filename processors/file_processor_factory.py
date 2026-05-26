import importlib
from pathlib import Path

class FileProcessorFactory:
    def __init__(self, db_handler, email_notifier, logger):
        self.db_handler = db_handler
        self.email_notifier = email_notifier
        self.logger = logger
    
    def get_processor(self, file_name: str):
        try:
            part_1 = file_name.split('_')[0]
            if part_1.isdigit():
                file_name = file_name.replace(str(part_1+'_'),"")
            # استخراج نام پردازشگر از نام فایل
            processor_name = self._extract_processor_name(file_name)
            
            # ایمپورت پویای ماژول پردازشگر
            module = importlib.import_module(f"processors.{processor_name}")
            processor_class = getattr(module, f"{processor_name}Processor")
            
            return processor_class(
                self.db_handler,
                self.email_notifier,
                self.logger
            )
        except (ImportError, AttributeError) as e:
            self.logger.error(f"Processor not found for {file_name}: {str(e)}")
            raise ValueError(f"No processor found for file: {file_name}")
    
    def _extract_processor_name(self, filename):
        """استخراج نام پردازشگر از نام فایل"""
        base_name = Path(filename).stem
        import re
        
        # حذف الگوهای تاریخ و زمان
        clean_name = re.sub(r'_\d{4}-\d{2}-\d{2}_\d{6}$', '', base_name)
        clean_name = re.sub(r'_\d{8,}.*$', '', clean_name)
        clean_name = clean_name.rstrip('_')
        parts = clean_name.split('_')
        print("parts",parts)
        if len(parts) > 1:
            if len(parts) == 3:
                if parts[2].isdigit():
                    if parts[1].isdigit():
                        clean_name = parts[0]
                    else:
                        clean_name = str(parts[0]+"_"+parts[1])
                else:
                    pass
            if len(parts) == 2:
                if parts[1].isdigit():
                    clean_name = parts[0]
                elif parts[0] == 'FRAMESCUTTING':
                    clean_name = parts[0]
                else:
                    clean_name = str(parts[0]+"_"+parts[1])

        print("clean_name",clean_name)
        # تبدیل به حروف بزرگ برای مطابقت با نام کلاس
        return clean_name.upper()  # اضافه شده: تبدیل به uppercase