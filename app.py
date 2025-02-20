from flask import Flask, request, jsonify, render_template
import json
import random
import os
import datetime
import shutil
import calendar
import pandas as pd
from dataclasses import dataclass
from typing import Dict, List, Optional
import logging
from dateutil.relativedelta import relativedelta

# ==========================
# Data Structure Definition
# ==========================

@dataclass
class MeterReading:
    meter_id: str
    reading_time: str
    meter_value: float

# ==========================
# Directory Manager: Handles folder and path management
# ==========================

class DirectoryManager:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.data_dir = os.path.join(base_dir, 'data')
        self.daily_readings_dir = os.path.join(self.data_dir, "daily_readings")
        self.monthly_readings_dir = os.path.join(self.data_dir, "month_readings")
        self.accounts_file = os.path.join(self.data_dir, "all_account.json")
        self.current_time_file = os.path.join(self.data_dir, "current_time.json")
        self.ensure_directories()

    def ensure_directories(self):
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.daily_readings_dir, exist_ok=True)
        os.makedirs(self.monthly_readings_dir, exist_ok=True)

    def get_month_directory(self, base: str, date: datetime.datetime) -> str:
        month_dir = os.path.join(base, date.strftime("%Y%m"))
        os.makedirs(month_dir, exist_ok=True)
        return month_dir

# ==========================
# Account Manager: Handles account loading, saving and registration
# ==========================

class AccountManager:
    def __init__(self, accounts_file: str):
        self.accounts_file = accounts_file

    def load_accounts(self) -> List[dict]:
        if os.path.exists(self.accounts_file):
            with open(self.accounts_file, "r", encoding="utf-8") as f:
                try:
                    accounts = json.load(f)
                    return accounts if isinstance(accounts, list) else []
                except json.JSONDecodeError:
                    return []
        return []

    def save_accounts(self, accounts: List[dict]):
        os.makedirs(os.path.dirname(self.accounts_file), exist_ok=True)
        with open(self.accounts_file, "w", encoding="utf-8") as f:
            json.dump(accounts, f, ensure_ascii=False, indent=2)

    def register_account(self, meter_id: str, area: str, dwelling: str, register_time: str) -> dict:
        accounts = self.load_accounts()
        if any(acc["meter_ID"] == meter_id for acc in accounts):
            raise ValueError("Meter ID already exists")
        account = {
            "meter_ID": meter_id,
            "area": area,
            "dwelling": dwelling,
            "register_time": register_time
        }
        accounts.append(account)
        self.save_accounts(accounts)
        return account

# ==========================
# Time Manager: Handles simulation time retrieval and updates
# ==========================

class TimeManager:
    def __init__(self, current_time_file: str):
        self.current_time_file = current_time_file

    def get_current_time(self) -> datetime.datetime:
        if os.path.exists(self.current_time_file):
            with open(self.current_time_file, "r") as f:
                data = json.load(f)
                return datetime.datetime.fromisoformat(data["current_time"])
        else:
            initial_time = datetime.datetime(2024, 5, 1)
            self.save_current_time(initial_time)
            return initial_time

    def save_current_time(self, current_time: datetime.datetime):
        with open(self.current_time_file, "w") as f:
            json.dump({"current_time": current_time.isoformat()}, f)

# ==========================
# Reading Generator: Generates meter readings and maintains latest readings and daily cache
# ==========================

class ReadingGenerator:
    def __init__(self, time_manager: TimeManager, account_manager: AccountManager):
        self.time_manager = time_manager
        self.account_manager = account_manager
        self.latest_readings: Dict[str, float] = {}
        self.daily_cache: List[MeterReading] = []

        self._initialize_latest_readings()

    def _initialize_latest_readings(self):
        """Initialize latest_readings from daily_cache file"""
        cache_file = os.path.join(os.path.dirname(self.time_manager.current_time_file), "daily_cache.json")
        
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cache_data = json.load(f)
                
                for meter_id, readings in cache_data.items():
                    if readings:  
                        sorted_readings = sorted(
                            readings, 
                            key=lambda x: x["reading_time"],
                            reverse=True  
                        )
                        self.latest_readings[meter_id] = sorted_readings[0]["meter_value"]
                        
            except (json.JSONDecodeError, FileNotFoundError) as e:
                print(f"Error loading daily_cache file: {str(e)}")

    def _calculate_next_time(
        self, current_time: datetime.datetime, increment_unit: str, increment_value: int
    ) -> datetime.datetime:
        if increment_unit == 'minutes':
            return current_time + datetime.timedelta(minutes=increment_value)
        elif increment_unit == 'hours':
            return current_time + datetime.timedelta(hours=increment_value)
        elif increment_unit == 'days':
            return current_time + datetime.timedelta(days=increment_value)
        elif increment_unit == 'months':
            next_month = current_time.month + increment_value
            next_year = current_time.year + (next_month - 1) // 12
            next_month = ((next_month - 1) % 12) + 1
            last_day_of_next_month = calendar.monthrange(next_year, next_month)[1]
            next_day = min(current_time.day, last_day_of_next_month)
            return current_time.replace(year=next_year, month=next_month, day=next_day)
        else:
            raise ValueError("Invalid time unit")

    def generate_readings_for_day(
        self, day_start: datetime.datetime, day_end: datetime.datetime
    ) -> List[dict]:
        """
        Generate data within the same day:
        - If start time is at midnight, skip 0:00-1:00 (maintenance period)
        - Generate a data point every 30 minutes until reaching day_end
        """
        accounts = self.account_manager.load_accounts()
        daily_readings = []
        # Normalize start time: if in maintenance period, start from 1:00
        current = day_start.replace(minute=0, second=0, microsecond=0)
        if current.hour == 0:
            current = current.replace(hour=1)
        
        while current < day_end:
            next_time = current + datetime.timedelta(minutes=30)
            # Exit if next time point exceeds end time
            if next_time > day_end:
                break
            # End generation for the day if next time point enters maintenance period
            if next_time.hour == 0:
                break

            for account in accounts:
                meter_id = account["meter_ID"]
                previous_value = self.latest_readings.get(meter_id, 0)
                increment = random.uniform(0, 1)
                meter_value = previous_value + increment
                self.latest_readings[meter_id] = meter_value

                reading = {
                    "meter_ID": meter_id,
                    "reading_time": next_time.isoformat(),
                    "meter_value": round(meter_value, 3)
                }
                daily_readings.append(reading)
                self.daily_cache.append(MeterReading(meter_id, next_time.isoformat(), round(meter_value, 3)))
            
            current = next_time

        return daily_readings

    def generate_readings(
        self, start_time: datetime.datetime, end_time: datetime.datetime
    ) -> List[dict]:
        """
        Generate data based on start and end times. If spanning multiple days,
        iterate through each day calling generate_readings_for_day.
        """
        readings = []
        # If within the same day, generate data directly
        if start_time.date() == end_time.date():
            return self.generate_readings_for_day(start_time, end_time)
        
        # Process first day
        first_day_end = datetime.datetime.combine(start_time.date(), datetime.time(23, 59, 59))
        readings.extend(self.generate_readings_for_day(start_time, first_day_end))

        # Process middle days
        next_day = start_time.date() + datetime.timedelta(days=1)
        while next_day < end_time.date():
            day_start = datetime.datetime.combine(next_day, datetime.time(0, 0))
            day_end = datetime.datetime.combine(next_day, datetime.time(23, 59, 59))
            readings.extend(self.generate_readings_for_day(day_start, day_end))
            next_day += datetime.timedelta(days=1)

        # Process last day
        last_day_start = datetime.datetime.combine(end_time.date(), datetime.time(0, 0))
        readings.extend(self.generate_readings_for_day(last_day_start, end_time))
        return readings

    def collect(self, increment_unit: str, increment_value: int) -> dict:
        current_time = self.time_manager.get_current_time()
        next_time = self._calculate_next_time(current_time, increment_unit, increment_value)
        readings = self.generate_readings(current_time, next_time)
        self.time_manager.save_current_time(next_time)
        return {
            "message": f"Readings collected from {current_time} to {next_time}",
            "readings_count": len(readings),
            "sample_readings": readings[:3] if readings else [],
            "new_time": next_time.isoformat()
        }

# ==========================
# Daily Processor: Organizes daily cache data and saves to JSON files
# ==========================

class DailyProcessor:
    def __init__(self, directory_manager: DirectoryManager):
        self.directory_manager = directory_manager

    def process(self, daily_cache: List[MeterReading], process_date: datetime.datetime):
        if not daily_cache:
            return

        daily_data = {}
        for reading in daily_cache:
            meter_id = reading.meter_id
            if meter_id not in daily_data:
                daily_data[meter_id] = {
                    "date": process_date.strftime("%Y-%m-%d"),
                    "readings": []
                }
            time_part = datetime.datetime.fromisoformat(reading.reading_time).strftime("%H:%M")
            daily_data[meter_id]["readings"].append({
                "time": time_part,
                "value": round(reading.meter_value, 3)
            })

        yesterday = process_date - datetime.timedelta(days=1)
        yesterday_file = self.get_daily_file_path(yesterday)
        yesterday_month_dir = self.directory_manager.get_month_directory(
            self.directory_manager.daily_readings_dir, yesterday
        )
        yesterday_monthly_file = os.path.join(
            yesterday_month_dir, 
            f"daily_{yesterday.strftime('%Y%m')}_detail.json"
        )

        monthly_data = {}
        if os.path.exists(yesterday_monthly_file):
            with open(yesterday_monthly_file, "r", encoding="utf-8") as f:
                try:
                    monthly_data = json.load(f)
                except json.JSONDecodeError:
                    monthly_data = {}

        if os.path.exists(yesterday_file):
            with open(yesterday_file, "r", encoding="utf-8") as f:
                try:
                    yesterday_data = json.load(f)
                    for meter_id, meter_data in yesterday_data.items():
                        if meter_id not in monthly_data:
                            monthly_data[meter_id] = []
                        monthly_data[meter_id].append(meter_data)
                    os.remove(yesterday_file)
                except json.JSONDecodeError:
                    pass

        os.makedirs(os.path.dirname(yesterday_monthly_file), exist_ok=True)
        with open(yesterday_monthly_file, "w", encoding="utf-8") as f:
            json.dump(monthly_data, f, ensure_ascii=False, indent=2)

        daily_file = self.get_daily_file_path(process_date)
        os.makedirs(os.path.dirname(daily_file), exist_ok=True)
        with open(daily_file, "w", encoding="utf-8") as f:
            json.dump(daily_data, f, ensure_ascii=False, indent=2)


    def get_daily_file_path(self, date: datetime.datetime) -> str:
        month_dir = self.directory_manager.get_month_directory(
            self.directory_manager.daily_readings_dir, date
        )
        return os.path.join(month_dir, f"readings_{date.strftime('%Y%m%d')}.json")
    
    def process_all(self, daily_cache: List[MeterReading]):
        if not daily_cache:
            return
        
        readings_by_date = {}
        for reading in daily_cache:
            date_str = datetime.datetime.fromisoformat(reading.reading_time).strftime("%Y-%m-%d")
            if date_str not in readings_by_date:
                readings_by_date[date_str] = []
            readings_by_date[date_str].append(reading)
        
        for date_str, readings in readings_by_date.items():
            process_date = datetime.datetime.fromisoformat(readings[-1].reading_time)
            self.process(readings, process_date)

# ==========================
# Monthly Processor: Archives monthly data, generates monthly consumption and cleans old data
# ==========================

class MonthlyProcessor:
    def __init__(self, directory_manager: DirectoryManager):
        self.directory_manager = directory_manager

    def archive(self, current_date: datetime.datetime):
        """
        Archive monthly data by processing daily_YYYYMM_detail.json from previous month
        Store monthly summary in year directories under month_readings
        """
        # Calculate dates
        first_of_current = current_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month = first_of_current - datetime.timedelta(days=1)
        last_month_first = last_month.replace(day=1)
        
        # Skip if before system start date
        if last_month_first < datetime.datetime(2024, 5, 1):
            return

        # Get paths
        last_month_dir = self.directory_manager.get_month_directory(
            self.directory_manager.daily_readings_dir,
            last_month_first
        )
        last_month_detail_file = os.path.join(
            last_month_dir,
            f"daily_{last_month_first.strftime('%Y%m')}_detail.json"
        )
        
        yesterday_file = os.path.join(
            last_month_dir,
            f"readings_{last_month.strftime('%Y%m%d')}.json"
        )
        monthly_detail_file = os.path.join(
            last_month_dir,
            f"daily_{last_month_first.strftime('%Y%m')}_detail.json"
        )

        if os.path.exists(yesterday_file):
            monthly_detail_data = {}
            if os.path.exists(monthly_detail_file):
                with open(monthly_detail_file, "r", encoding="utf-8") as f:
                    try:
                        monthly_detail_data = json.load(f)
                    except json.JSONDecodeError:
                        monthly_detail_data = {}

            with open(yesterday_file, "r", encoding="utf-8") as f:
                try:
                    yesterday_data = json.load(f)
                    for meter_id, meter_data in yesterday_data.items():
                        if meter_id not in monthly_detail_data:
                            monthly_detail_data[meter_id] = []
                        monthly_detail_data[meter_id].append(meter_data)
                except json.JSONDecodeError:
                    pass

            os.makedirs(os.path.dirname(monthly_detail_file), exist_ok=True)
            with open(monthly_detail_file, "w", encoding="utf-8") as f:
                json.dump(monthly_detail_data, f, ensure_ascii=False, indent=2)

            os.remove(yesterday_file)


        # Create year directory in month_readings
        year_dir = os.path.join(
            self.directory_manager.monthly_readings_dir,
            last_month_first.strftime("%Y")
        )
        os.makedirs(year_dir, exist_ok=True)
        
        # Monthly summary file path now includes year directory
        monthly_summary_file = os.path.join(
            year_dir,
            f"month_readings_{last_month_first.strftime('%Y%m')}.json"
        )

        # Read and process daily detail file
        if os.path.exists(last_month_detail_file):
            try:
                with open(last_month_detail_file, "r", encoding="utf-8") as f:
                    detail_data = json.load(f)
                
                monthly_data = {}

                # Process each meter's data
                for meter_id, daily_readings in detail_data.items():
                    # Sort all readings by date for this meter
                    all_readings = []
                    for day_data in daily_readings:
                        date = day_data["date"]
                        for reading in day_data["readings"]:
                            all_readings.append({
                                "datetime": f"{date} {reading['time']}",
                                "date": date,
                                "time": reading["time"],
                                "value": reading["value"]
                            })
                    
                    if all_readings:
                        # Sort readings by datetime
                        all_readings.sort(key=lambda x: x["datetime"])
                        
                        # Keep only first and last readings
                        month_key = last_month_first.strftime("%Y-%m")
                        if meter_id not in monthly_data:
                            monthly_data[meter_id] = {}
                        
                        # Store first and last readings in the requested format
                        monthly_data[meter_id][month_key] = {
                            "readings": [
                                {
                                    "date": all_readings[0]["date"],
                                    "time": all_readings[0]["time"],
                                    "value": all_readings[0]["value"]
                                },
                                {
                                    "date": all_readings[-1]["date"],
                                    "time": all_readings[-1]["time"],
                                    "value": all_readings[-1]["value"]
                                }
                            ]
                        }

                # Save monthly summary
                os.makedirs(os.path.dirname(monthly_summary_file), exist_ok=True)
                with open(monthly_summary_file, "w", encoding="utf-8") as f:
                    json.dump(monthly_data, f, ensure_ascii=False, indent=2)

            except Exception as e:
                print(f"Error processing monthly archive: {str(e)}")

        # Clean up old readings
        self._cleanup_old_readings(first_of_current)

    def _cleanup_old_readings(self, current_month_first: datetime.datetime):
        """
        Clean up all daily data folders except for the current month and previous month.
        
        param current_month_first: First day of current month
        """
        # Calculate the previous month (to keep)
        previous_month = current_month_first - datetime.timedelta(days=1)  # Last day of previous month
        previous_month = previous_month.replace(day=1)  # First day of previous month
        
        # Get the cutoff date (anything before this will be deleted)
        cutoff_date = previous_month  # We want to keep current_month and previous_month
        
        # List all folders in the daily readings directory
        daily_readings_dir = self.directory_manager.daily_readings_dir
        if os.path.exists(daily_readings_dir):
            for folder_name in os.listdir(daily_readings_dir):
                try:
                    # Parse folder name as date (expected format: YYYYMM)
                    folder_date = datetime.datetime.strptime(folder_name, '%Y%m')
                    
                    # If folder date is before previous month, delete it
                    if folder_date < cutoff_date:
                        folder_path = os.path.join(daily_readings_dir, folder_name)
                        if os.path.exists(folder_path):
                            shutil.rmtree(folder_path)
                            print(f"Deleted old folder: {folder_name}")
                except ValueError:
                    # Skip folders that don't match the expected date format
                    print(f"Skipping invalid folder name: {folder_name}")
                except Exception as e:
                    print(f"Error while trying to delete folder {folder_name}: {str(e)}")


    def archive_all(self, start_date: datetime.datetime, current_date: datetime.datetime):
        """
        Process all months between start_date and current_date
        
        Args:
            start_date: The start time to process from
            current_date: The current time to process until
        """
        start = start_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = current_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        if start > end:
            start, end = end, start

        # Traverse from the start time to the end time
        current = start + relativedelta(months=1)  # Starting next month
        while current <= end:
            self.archive(current)
            current += relativedelta(months=1)
# ==========================
# CacheProcessor
# ==========================
class CacheProcessor:
    def __init__(self, directory_manager: DirectoryManager):
        self.directory_manager = directory_manager
        self.cache_file = os.path.join(self.directory_manager.data_dir, "daily_cache.json")

    def save_cache(self, daily_cache: List[MeterReading]):
        """
        Save daily_cache to a file
        """
        if not daily_cache:
            return

        # Convert a MeterReading object to a serializable dictionary
        cache_data = {}
        for reading in daily_cache:
            meter_id = reading.meter_id
            if meter_id not in cache_data:
                cache_data[meter_id] = []
            
            cache_data[meter_id].append({
                "reading_time": reading.reading_time,
                "meter_value": reading.meter_value
            })

        # Save to file
        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)

    def load_cache(self) -> List[MeterReading]:
        """
        Load daily_cache from file
        """
        if not os.path.exists(self.cache_file):
            return []

        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                cache_data = json.load(f)

            # Convert back to a MeterReading object
            daily_cache = []
            for meter_id, readings in cache_data.items():
                for reading in readings:
                    daily_cache.append(MeterReading(
                        meter_id=meter_id,
                        reading_time=reading["reading_time"],
                        meter_value=reading["meter_value"]
                    ))

            return daily_cache
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def clear_cache(self):
        """
        Clear cache files
        """
        if os.path.exists(self.cache_file):
            os.remove(self.cache_file)

# ==========================
# Smart Meter System: Facade class combining all modules
# ==========================

class SmartMeterSystem:
    def __init__(self, base_dir: str):
        self.directory_manager = DirectoryManager(base_dir)
        self.account_manager = AccountManager(self.directory_manager.accounts_file)
        self.time_manager = TimeManager(self.directory_manager.current_time_file)
        self.reading_generator = ReadingGenerator(self.time_manager, self.account_manager)
        self.daily_processor = DailyProcessor(self.directory_manager)
        self.monthly_processor = MonthlyProcessor(self.directory_manager)
        self.cache_processor = CacheProcessor(self.directory_manager)

        self.reading_generator.daily_cache = self.cache_processor.load_cache()

    def register_meter(self, meter_id: str, area: str, dwelling: str) -> dict:
        current_time = self.time_manager.get_current_time()
        formatted_time = current_time.strftime("%Y-%m-%dT%H:%M:%S")
        account = self.account_manager.register_account(meter_id, area, dwelling, formatted_time)
        # Initialize meter reading
        self.reading_generator.latest_readings[meter_id] = 0
        self.reading_generator.daily_cache.append(MeterReading(meter_id, formatted_time, 0))
        return account

    def collect_readings(self, increment_unit: str = 'days', increment_value: int = 1) -> dict:
        # Record current time before collection
        old_time = self.time_manager.get_current_time()
        
        # Collect new readings
        result = self.reading_generator.collect(increment_unit, increment_value)
        new_time = datetime.datetime.fromisoformat(result["new_time"])

        self.cache_processor.save_cache(self.reading_generator.daily_cache)
        
        # If day changes during collection
        if old_time.date() != new_time.date():
            # Get all completed days (excluding the current day)
            readings_by_date = {}
            for reading in self.reading_generator.daily_cache:
                reading_date = datetime.datetime.fromisoformat(reading.reading_time).date()
                # Only process readings from completed days
                if reading_date < new_time.date():
                    if reading_date not in readings_by_date:
                        readings_by_date[reading_date] = []
                    readings_by_date[reading_date].append(reading)
            
            # Process each completed day
            for date, readings in readings_by_date.items():
                self.daily_processor.process(readings, datetime.datetime.combine(date, datetime.time()))
            
            # Keep only current day's readings in the cache
            self.reading_generator.daily_cache = [
                reading for reading in self.reading_generator.daily_cache
                if datetime.datetime.fromisoformat(reading.reading_time).date() == new_time.date()
            ]
        
        self.cache_processor.save_cache(self.reading_generator.daily_cache)

        # If month changes during collection
        if old_time.month != new_time.month:
            self.monthly_processor.archive_all(old_time,new_time)
        
        return result

    def reset_system(self) -> bool:
        try:
            # Clear daily_readings and monthly_readings directories
            for directory in [self.directory_manager.daily_readings_dir, self.directory_manager.monthly_readings_dir]:
                if os.path.exists(directory):
                    shutil.rmtree(directory)
                os.makedirs(directory)
            # Reset account file
            with open(self.directory_manager.accounts_file, 'w', encoding='utf-8') as f:
                json.dump([], f, ensure_ascii=False, indent=2)
            # Reset time
            self.time_manager.save_current_time(datetime.datetime(2024, 5, 1))
            # Clear cache
            self.reading_generator.latest_readings.clear()
            self.reading_generator.daily_cache.clear()
            self.cache_processor.clear_cache()
            return True
        except Exception as e:
            import traceback
            print("Reset failed:")
            traceback.print_exc()
            return False

# ==========================
# Flask Application
# ==========================
app = Flask(__name__, 
    template_folder='templates',  # Specify the templates directory
    static_folder='static'         # Specify the static files directory
)
meter_system = SmartMeterSystem(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = "data/daily_readings"
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

@app.route("/")
def index():
    """Render the index page."""
    return render_template("index.html")

@app.route('/collect')
def collect():
    """Render the collection page."""
    return render_template('collect.html')

@app.route("/register", methods=["GET", "POST"])
def register():
    # GET request: Display the registration page.
    if request.method == "GET":
        return render_template("register.html")
        
    # POST request: Process the registration logic.
    try:
        data = request.get_json()
        account = meter_system.register_meter(
            data["meterId"],
            data["area"],
            data["dwelling"]
        )
        return jsonify({"success": True, "account": account})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400

@app.route("/current_time", methods=["GET"])
def get_current_time():
    current_time = meter_system.time_manager.get_current_time()
    return jsonify({
        "Current Simulation Time": {
            "Date": current_time.strftime("%Y-%m-%d"),
            "Time": current_time.strftime("%H:%M:%S"),
            "Weekday": current_time.strftime("%A")
        }
    })

@app.route("/meter_reading", methods=["POST"])
def meter_reading():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
            
        unit = data.get('unit', 'days')
        try:
            value = int(data.get('value', 1))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid value format"}), 400
            
        result = meter_system.collect_readings(unit, value)
        return jsonify(result), 200
        
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        import traceback
        print("Error in meter_reading:", str(e))
        print(traceback.format_exc())
        return jsonify({
            "error": "Internal server error",
            "message": str(e)
        }), 500

@app.route("/api/areas", methods=["GET"])
def get_areas():
    """Get area data from a JSON file."""
    area_data_file = os.path.join(app.static_folder, 'js', 'area_data.json')
    try:
        with open(area_data_file, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    except FileNotFoundError:
        return jsonify({"error": "Area data file not found"}), 404
    except json.JSONDecodeError:
        return jsonify({"error": "Invalid area data format"}), 500


@app.route("/query")
def query_page():
    return render_template("query.html")

@app.route("/validate_meter", methods=["POST"])
def validate_meter():
    """Validate if meter ID exists in the system"""
    try:
        data = request.get_json()
        meter_id = data.get("meterId")
        
        if not meter_id:
            return jsonify({"error": "Meter ID is required"}), 400
            
        if check_meter_exists(meter_id):
            return jsonify({"success": True}), 200
        else:
            return jsonify({"error": "Invalid Meter ID"}), 404
            
    except Exception as e:
        print(f"Validation error: {str(e)}")  
        return jsonify({"error": str(e)}), 500

def read_current_time():
    """Read current time from JSON file"""
    with open("data/current_time.json", 'r') as f:
        time_data = json.load(f)
        current_date = datetime.datetime.fromisoformat(time_data["current_time"])
        return current_date

def check_meter_exists(meter_id):
    try:
        accounts_file = meter_system.directory_manager.accounts_file
        
        if os.path.exists(accounts_file):
            with open(accounts_file, 'r') as f:
                accounts = json.load(f)
                return any(account['meter_ID'] == meter_id for account in accounts)
        else:
            print(f"Account file not found: {accounts_file}")
            return False
            
    except Exception as e:
        print(f"Error checking meter existence: {str(e)}")
        return False

@app.route("/query_usage")
def query_usage():
    """Query power usage data based on meter ID and time range"""
    try:
        meter_id = request.args.get("meter_id")
        time_range = request.args.get("time_range")
        
        if not meter_id or not time_range:
            return jsonify({"error": "Meter ID and time range are required"}), 400
            
        current_date = read_current_time()
        dates = []
        usage = []
        
        if time_range == "today":
            # Get today's readings from cache first
            current_date_str = current_date.strftime("%Y-%m-%d")
            
            # Filter today's readings from cache
            today_readings = [
                reading for reading in meter_system.reading_generator.daily_cache
                if meter_id == reading.meter_id and 
                datetime.datetime.fromisoformat(reading.reading_time).date() == current_date.date()
            ]
            
            if today_readings:
                # Sort readings by time
                today_readings.sort(key=lambda x: x.reading_time)
                
                # Calculate intervals
                prev_value = None
                for reading in today_readings:
                    time = datetime.datetime.fromisoformat(reading.reading_time).strftime("%H:%M")
                    current_value = reading.meter_value
                    if prev_value is not None:
                        dates.append(time)
                        usage.append(round(current_value - prev_value, 3))
                    prev_value = current_value
            else:
                # Fallback to file if cache is empty
                date_str = current_date.strftime("%Y%m%d")
                month_folder = current_date.strftime("%Y%m")
                file_path = os.path.join("data/daily_readings", month_folder, f"readings_{date_str}.json")
                
                if os.path.exists(file_path):
                    with open(file_path, 'r') as f:
                        data = json.load(f)
                        if meter_id in data:
                            readings = data[meter_id]["readings"]
                            prev_value = None
                            for reading in readings:
                                time = reading["time"]
                                current_value = reading["value"]
                                if prev_value is not None:
                                    dates.append(time)
                                    usage.append(round(current_value - prev_value, 3))
                                prev_value = current_value
                            
        elif time_range in ["last_7_days", "this_month", "last_month"]:
            if time_range == "last_7_days":
                start_date = current_date - datetime.timedelta(days=6)
                end_date = current_date
            elif time_range == "this_month":
                start_date = current_date.replace(day=1)
                end_date = current_date
            else:  # last_month
                first_of_month = current_date.replace(day=1)
                start_date = (first_of_month - datetime.timedelta(days=1)).replace(day=1)
                end_date = first_of_month - datetime.timedelta(days=1)
            
            # Process data based on date range
            current_date = start_date
            while current_date <= end_date:
                month_folder = current_date.strftime("%Y%m")
                date_str = current_date.strftime("%Y%m%d")
                
                # Check if data is in daily readings
                daily_path = os.path.join("data/daily_readings", month_folder, f"readings_{date_str}.json")
                monthly_path = os.path.join("data/daily_readings", month_folder, f"daily_{month_folder}_detail.json")
                hist_monthly_path = os.path.join("data/month_readings", current_date.strftime("%Y"), f"month_readings_{month_folder}.json")
                
                daily_usage = None
                
                # For today's data, check cache first
                if current_date.date() == read_current_time().date():
                    today_readings = [
                        reading for reading in meter_system.reading_generator.daily_cache
                        if meter_id == reading.meter_id and 
                        datetime.datetime.fromisoformat(reading.reading_time).date() == current_date.date()
                    ]
                    if today_readings:
                        today_readings.sort(key=lambda x: x.reading_time)
                        if len(today_readings) >= 2:
                            daily_usage = today_readings[-1].meter_value - today_readings[0].meter_value
                
                # If not found in cache, check files
                if daily_usage is None:
                    if os.path.exists(daily_path):
                        with open(daily_path, 'r') as f:
                            data = json.load(f)
                            if meter_id in data:
                                readings = data[meter_id]["readings"]
                                if len(readings) >= 2:
                                    daily_usage = readings[-1]["value"] - readings[0]["value"]
                    
                    elif os.path.exists(monthly_path):
                        with open(monthly_path, 'r') as f:
                            data = json.load(f)
                            if meter_id in data:
                                for day_data in data[meter_id]:
                                    if day_data["date"] == current_date.strftime("%Y-%m-%d"):
                                        readings = day_data["readings"]
                                        if len(readings) >= 2:
                                            daily_usage = readings[-1]["value"] - readings[0]["value"]
                                        break
                    
                    elif os.path.exists(hist_monthly_path):
                        with open(hist_monthly_path, 'r') as f:
                            data = json.load(f)
                            if meter_id in data:
                                month_key = current_date.strftime("%Y-%m")
                                if month_key in data[meter_id]:
                                    readings = data[meter_id][month_key]["readings"]
                                    start_reading = None
                                    end_reading = None
                                    for reading in readings:
                                        reading_date = datetime.datetime.strptime(reading["date"], "%Y-%m-%d").date()
                                        if reading_date == current_date.date():
                                            if start_reading is None:
                                                start_reading = reading["value"]
                                            end_reading = reading["value"]
                                    if start_reading is not None and end_reading is not None:
                                        daily_usage = end_reading - start_reading
                
                if daily_usage is not None:
                    dates.append(current_date.strftime("%Y-%m-%d"))
                    usage.append(round(daily_usage, 3))
                
                current_date += datetime.timedelta(days=1)
        
        return jsonify({
            "dates": dates,
            "usage": usage
        })
        
    except Exception as e:
        print(f"Query error: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/monthly_history")
def monthly_history():
    """Get monthly usage history for a meter"""
    try:
        meter_id = request.args.get("meter_id")
        if not meter_id:
            return jsonify({"error": "Meter ID is required"}), 400
        
        current_date = read_current_time()
        months = []
        usage = []
        days = []
        
        # Start from the current month and go back in time
        for i in range(12):  # Show up to 12 months of history
            check_date = current_date - datetime.timedelta(days=30*i)
            month_folder = check_date.strftime("%Y%m")
            year_folder = check_date.strftime("%Y")
            
            # Try to find data in monthly readings first
            monthly_file = os.path.join("data/month_readings", year_folder, f"month_readings_{month_folder}.json")
            if os.path.exists(monthly_file):
                with open(monthly_file, 'r') as f:
                    data = json.load(f)
                    if meter_id in data:
                        month_key = check_date.strftime("%Y-%m")
                        if month_key in data[meter_id]:
                            readings = data[meter_id][month_key]["readings"]
                            if len(readings) >= 2:  # Make sure you have readings at the beginning and end of the month
                                # Calculate monthly usage
                                month_usage = readings[-1]["value"] - readings[0]["value"]
                                months.append(month_key)
                                usage.append(round(month_usage, 3))
                                # Calculate the number of days between the first and last dates
                                start_date = datetime.datetime.strptime(readings[0]["date"], "%Y-%m-%d")
                                end_date = datetime.datetime.strptime(readings[-1]["date"], "%Y-%m-%d")
                                days_count = (end_date - start_date).days + 1  # +1 Because it includes the first and last two days.
                                days.append(days_count)
            
            # If the monthly file is not found, check the daily readings file
            else:
                monthly_detail = os.path.join("data/daily_readings", month_folder, f"daily_{month_folder}_detail.json")
                if os.path.exists(monthly_detail):
                    with open(monthly_detail, 'r') as f:
                        data = json.load(f)
                        if meter_id in data:
                            first_day = data[meter_id][0]["readings"][0]["value"]
                            last_day = data[meter_id][-1]["readings"][-1]["value"]
                            month_usage = last_day - first_day
                            months.append(check_date.strftime("%Y-%m"))
                            usage.append(round(month_usage, 3))
                            days.append(len(data[meter_id]))
        
        # Sorting
        months_sorted = []
        usage_sorted = []
        days_sorted = []
        for m, u, d in sorted(zip(months, usage, days)):
            months_sorted.append(m)
            usage_sorted.append(u)
            days_sorted.append(d)
        
        return jsonify({
            "months": months_sorted,
            "usage": usage_sorted,
            "days": days_sorted
        })
        
    except Exception as e:
        print(f"Monthly history error: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route("/get_stats")
def get_stats():
    """Get power usage statistics based on meter ID"""
    try:
        meter_id = request.args.get("meter_id")
        if not meter_id:
            return jsonify({"error": "Meter ID is required"}), 400
            
        current_date = read_current_time()
        stats = {
            "last30Min": "-",
            "today": "-",
            "week": "-",
            "month": 0
        }
        
        # Get today's readings from cache first
        today_readings = [
            reading for reading in meter_system.reading_generator.daily_cache
            if meter_id == reading.meter_id and 
            datetime.datetime.fromisoformat(reading.reading_time).date() == current_date.date()
        ]

        if today_readings:
            # Sort readings by time
            today_readings.sort(key=lambda x: x.reading_time)
            
            # Calculate last30Min (using last two readings)
            if len(today_readings) >= 2:
                stats["last30Min"] = round(today_readings[-1].meter_value - today_readings[-2].meter_value, 1)
            
            # Calculate today's usage
            if len(today_readings) >= 2:
                stats["today"] = round(today_readings[-1].meter_value - today_readings[0].meter_value, 1)

        # Calculate week usage
        if len(today_readings) > 0:
            # Get latest value from cache
            latest_value = today_readings[-1].meter_value
            
            # Calculate date from 7 days ago at 1:00
            week_start = current_date - datetime.timedelta(days=6)
            week_start = week_start.replace(hour=1, minute=0, second=0, microsecond=0)
            
            month_folder = week_start.strftime("%Y%m")
            
            # Try to find the week start reading in monthly detail file
            monthly_path = os.path.join("data/daily_readings", month_folder, f"daily_{month_folder}_detail.json")
            
            if os.path.exists(monthly_path):
                with open(monthly_path, 'r') as f:
                    data = json.load(f)
                    if meter_id in data:
                        for day_data in data[meter_id]:
                            if day_data["date"] == week_start.strftime("%Y-%m-%d"):
                                # Get the first reading of that day
                                if day_data["readings"]:
                                    start_value = day_data["readings"][0]["value"]
                                    stats["week"] = round(latest_value - start_value, 1)
                                break

        # Calculate month usage
        month_start = current_date.replace(day=1)
        month_folder = current_date.strftime("%Y%m")
        year_folder = current_date.strftime("%Y")
        
        # Try monthly readings first
        hist_monthly_path = os.path.join("data/month_readings", year_folder, f"month_readings_{month_folder}.json")
        if os.path.exists(hist_monthly_path):
            with open(hist_monthly_path, 'r') as f:
                data = json.load(f)
                if meter_id in data:
                    month_key = current_date.strftime("%Y-%m")
                    if month_key in data[meter_id]:
                        readings = data[meter_id][month_key]["readings"]
                        if len(readings) >= 2:
                            stats["month"] = round(readings[-1]["value"] - readings[0]["value"], 1)
        
        # If not in monthly readings, calculate from daily readings
        if stats["month"] == 0:
            monthly_detail = os.path.join("data/daily_readings", month_folder, f"daily_{month_folder}_detail.json")
            if os.path.exists(monthly_detail):
                with open(monthly_detail, 'r') as f:
                    data = json.load(f)
                    if meter_id in data:
                        # Get the first and last readings of the month
                        first_day = data[meter_id][0]["readings"][0]["value"]
                        last_day = data[meter_id][-1]["readings"][-1]["value"]
                        stats["month"] = round(last_day - first_day, 1)

        return jsonify(stats)
        
    except Exception as e:
        print(f"Stats error: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/reset')
def reset():
    """Reset the system."""
    if meter_system.reset_system():
        return """
        <script>
            alert('Reset Success!');
            window.location.href = '/';
        </script>
        """
    else:
        return """
        <script>
            alert('Reset Failed');
            window.location.href = '/';
        </script>
        """

if __name__ == "__main__":
    app.run()