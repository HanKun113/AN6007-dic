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
        Clean daily data by directly removing the folder of two months ago.
        param current_month_first: First day of current month (e.g., if processing May data in June, this would be June 1st)
        """
        # Calculate two months ago date
        two_months_ago = current_month_first - datetime.timedelta(days=1)  # Last day of previous month
        two_months_ago = two_months_ago.replace(day=1)  # First day of previous month
        two_months_ago = two_months_ago - datetime.timedelta(days=1)  # Last day of two months ago
        two_months_ago = two_months_ago.replace(day=1)  # First day of two months ago
        
        # Get the folder name to delete
        folder_to_delete = two_months_ago.strftime('%Y%m')
        folder_path = os.path.join(self.directory_manager.daily_readings_dir, folder_to_delete)
        
        # Delete if exists
        if os.path.exists(folder_path):
            shutil.rmtree(folder_path)
            print(f"Deleted folder: {folder_to_delete}")  # Debug log

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
        result = self.reading_generator.collect(increment_unit, increment_value)
        # Archive daily_cache data by date
        self.daily_processor.process_all(self.reading_generator.daily_cache)
        # Clear cache
        self.reading_generator.daily_cache.clear()
        new_time = datetime.datetime.fromisoformat(result["new_time"])
        # If month changes during collection, trigger archiving (archive data from two months ago)
        if old_time.month != new_time.month:
            self.monthly_processor.archive(new_time)
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
        current_date = read_current_time()
        
        for i in range(7):
            check_date = current_date - datetime.timedelta(days=i)
            month_folder = check_date.strftime("%Y%m")
            file_path = os.path.join(DATA_DIR, month_folder, f"readings_{check_date.strftime('%Y%m%d')}.json")
            print(f"Checking file: {file_path}") 
            
            if os.path.exists(file_path):
                with open(file_path, 'r') as f:
                    data = json.load(f)
                    if meter_id in data:
                        return True
        
        month_folder = current_date.strftime("%Y%m")
        folder_path = os.path.join(DATA_DIR, month_folder)
        
        if os.path.exists(folder_path):
            for filename in os.listdir(folder_path):
                if filename.endswith('.json'):
                    file_path = os.path.join(folder_path, filename)
                    print(f"Checking monthly file: {file_path}") 
                    
                    with open(file_path, 'r') as f:
                        data = json.load(f)
                        if meter_id in data:
                            return True
        
        return False
        
    except Exception as e:
        print(f"Error checking meter existence: {str(e)}") 
        return False

@app.route("/query_usage", methods=["GET"])   
def query_usage():
    try:
        meter_id = request.args.get("meter_id")
        time_range = request.args.get("time_range")

        if not meter_id or not time_range:
            return jsonify({"error": "Meter ID and time range are required"}), 400

        current_date = read_current_time()
        
        # Get date range based on selection
        dates = get_date_range(time_range, current_date)
        if not dates:
            return jsonify({"error": "Invalid time range"}), 400

        # Load and process data
        all_data = load_meter_data(meter_id, dates)
        if not all_data:
            return jsonify({"error": "No data available for the selected period"}), 404

        # Process the data
        results = process_usage_data(all_data, time_range)
        
        return jsonify(results)

    except Exception as e:
        print(f"Error processing request: {str(e)}")
        return jsonify({"error": "An error occurred while processing your request"}), 500

def get_date_range(time_range, current_date):
    """Generate list of dates based on selected time range"""
    if time_range == "today":
        return [current_date.strftime("%Y-%m-%d")]
    
    elif time_range == "last_7_days":
        return [(current_date - datetime.timedelta(days=i)).strftime("%Y-%m-%d") 
                for i in range(7)]
    
    elif time_range == "this_month":
        return [current_date.replace(day=i).strftime("%Y-%m-%d") 
                for i in range(1, current_date.day + 1)]
    
    elif time_range == "last_month":
        last_month = (current_date.replace(day=1) - datetime.timedelta(days=1))
        last_month_days = (current_date.replace(day=1) - datetime.timedelta(days=1)).day
        return [last_month.replace(day=i).strftime("%Y-%m-%d") 
                for i in range(1, last_month_days + 1)]
    
    return None

def load_meter_data(meter_id, date_list):
    """Load meter readings from JSON files"""
    all_readings = []
    
    for date_str in date_list:
        date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        month_folder = date_obj.strftime("%Y%m")
        file_path = os.path.join(DATA_DIR, month_folder, f"readings_{date_obj.strftime('%Y%m%d')}.json")
        
        try:
            if os.path.exists(file_path):
                with open(file_path, 'r') as f:
                    data = json.load(f)
                    if meter_id in data:
                        meter_data = data[meter_id]
                        date = meter_data["date"]
                        for reading in meter_data["readings"]:
                            all_readings.append({
                                "date": date,
                                "time": reading["time"],
                                "value": reading["value"]
                            })
        except Exception as e:
            print(f"Error reading file {file_path}: {str(e)}")
            continue
    
    return all_readings

def process_usage_data(all_data, time_range):
    """Process meter readings into usage data"""
    try:
        # Convert to DataFrame
        df = pd.DataFrame(all_data)
        
        # Create datetime column
        df["datetime"] = pd.to_datetime(df["date"] + " " + df["time"])
        
        # Sort by datetime
        df.sort_values(by="datetime", inplace=True)
        
        # Calculate usage (difference between consecutive readings)
        df["usage"] = df["value"].diff().fillna(0)
        
        # Remove negative values (potential meter resets)
        df.loc[df["usage"] < 0, "usage"] = 0
        
        # Group data based on time range
        if time_range == "today":
            df["time_label"] = df["datetime"].dt.strftime("%H:%M")
            result_df = df.groupby("time_label").agg({
                "usage": "sum"
            }).reset_index()
            x_labels = result_df["time_label"].tolist()
        else:
            df["date_label"] = df["datetime"].dt.strftime("%Y-%m-%d")
            result_df = df.groupby("date_label").agg({
                "usage": "sum"
            }).reset_index()
            x_labels = result_df["date_label"].tolist()

        y_values = result_df["usage"].round(3).tolist()
        
        return {
            "dates": x_labels,
            "usage": y_values,
            "total_usage": round(sum(y_values), 3),
            "average_usage": round(sum(y_values) / len(y_values), 3) if y_values else 0
        }
        
    except Exception as e:
        print(f"Error processing usage data: {str(e)}")
        raise


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