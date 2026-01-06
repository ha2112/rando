import os
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from dotenv import load_dotenv

# ============================================================================
# CONFIGURATION
# ============================================================================

load_dotenv()
SOURCE_FOLDER = os.getenv("DJI_FOOTAGE_FOLDER_PATH", "")

# Set to True to see what would happen without actually renaming files
input_for_dry_run = input("Dry run (theoretical run): y/n\n").lower()
DRY_RUN = False if input_for_dry_run == "n" else True

# ============================================================================
# MAIN SCRIPT
# ============================================================================

def parse_dji_filename(filename):
    """
    Parse DJI filename to extract date/time components.
    Format: DJI_YYYYMMDDHHMMSS_XXXX_D.EXT
    """
    pattern = r'DJI_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})_(\d{4})_D\.(\w+)'
    match = re.match(pattern, filename, re.IGNORECASE)
    
    if match:
        year, month, day, hour, minute, second, sequence, ext = match.groups()
        return {
            'year': year,
            'short_year': year[-2:], # last two digits
            'month': month,
            'day': day,
            'hour': hour,
            'minute': minute,
            'second': second,
            'sequence': sequence,
            'extension': ext.lower(),
            'date': f"{year}-{month}-{day}",
            'timestamp': f"{year}{month}{day}{hour}{minute}{second}"
        }
    return None


def create_new_filename(parsed_info, description, counter):
    """
    Creates filename: [dd-mm-yy_hh-mm-ss]_title_in_lowercase_#X
    """
    # Format components
    date_part = f"{parsed_info['day']}-{parsed_info['month']}-{parsed_info['short_year']}"
    time_part = f"{parsed_info['hour']}-{parsed_info['minute']}-{parsed_info['second']}"
    
    # Process title: lowercase and replace spaces with underscores for cleanliness
    clean_title = description.lower().replace(" ", "_")
    
    ext = parsed_info['extension']
    
    return f"[{date_part}_{time_part}]_{clean_title}_#{counter}.{ext}"


def find_existing_folder_for_date(source_path, date):
    """
    Check if a folder already exists for [YYYY.MM.DD]
    """
    source_dir = Path(source_path)
    display_date = date.replace('-', '.')
    prefix = f"[{display_date}]"
    
    for item in source_dir.iterdir():
        if item.is_dir() and item.name.startswith(prefix):
            description = item.name[len(prefix):].strip()
            return item, description
    return None, None


def get_highest_part_number(folder_path):
    """
    Scans folder to find the current highest #X suffix.
    """
    if not folder_path.exists():
        return 0
    
    highest = 0
    # Regex looks for # followed by digits at the end of the filename (before extension)
    pattern = re.compile(r'_#(\d+)\.\w+$')
    
    for file_path in folder_path.iterdir():
        if file_path.is_file():
            match = pattern.search(file_path.name)
            if match:
                part_num = int(match.group(1))
                highest = max(highest, part_num)
    
    return highest


def group_files_by_date(source_path):
    grouped_files = defaultdict(list)
    source_dir = Path(source_path)
    
    if not source_dir.exists():
        print(f"❌ Error: Source folder does not exist: {source_path}")
        return grouped_files
    
    for file_path in source_dir.iterdir():
        if file_path.is_file():
            parsed = parse_dji_filename(file_path.name)
            if parsed:
                grouped_files[parsed['date']].append({
                    'original_path': file_path,
                    'parsed': parsed
                })
    return grouped_files


def process_files(source_path):
    print("=" * 70)
    print(f"MODE: {'DRY RUN' if DRY_RUN else 'LIVE MODE'}")
    print("=" * 70)
    
    grouped_files = group_files_by_date(source_path)
    if not grouped_files:
        print("❌ No DJI files found.")
        return
    
    for date in sorted(grouped_files.keys()):
        files = grouped_files[date]
        print(f"\n📁 Processing date: {date}")
        
        existing_folder, existing_description = find_existing_folder_for_date(source_path, date)
        display_date = date.replace('-', '.')
        
        if existing_folder:
            print(f"   Found existing folder: {existing_folder.name}")
            use_existing = input(f"   Use this folder? (Y/n): ").strip().lower()
            if use_existing in ['', 'y', 'yes']:
                description = existing_description
                folder_path = existing_folder
            else:
                description = input(f"   Enter NEW description for {display_date}: ").strip()
                folder_name = f"[{display_date}] {description}"
                folder_path = Path(source_path) / folder_name
        else:
            description = input(f"   Enter description for {display_date}: ").strip()
            folder_name = f"[{display_date}] {description}"
            folder_path = Path(source_path) / folder_name

        if not DRY_RUN:
            folder_path.mkdir(exist_ok=True)

        # Sync/Group by timestamp (video + audio pairs)
        timestamp_groups = defaultdict(list)
        for f_info in files:
            timestamp_groups[f_info['parsed']['timestamp']].append(f_info)
        
        # Determine starting counter
        counter = get_highest_part_number(folder_path) + 1
        
        for timestamp in sorted(timestamp_groups.keys()):
            for file_info in timestamp_groups[timestamp]:
                old_path = file_info['original_path']
                
                new_filename = create_new_filename(
                    file_info['parsed'], 
                    description, 
                    counter
                )
                new_path = folder_path / new_filename
                
                if DRY_RUN:
                    print(f"   [DRY RUN] {old_path.name} -> {new_filename}")
                else:
                    try:
                        old_path.rename(new_path)
                        print(f"   ✅ {new_filename}")
                    except Exception as e:
                        print(f"   ❌ Error renaming {old_path.name}: {e}")
            
            counter += 1

if __name__ == "__main__":
    if not SOURCE_FOLDER:
        print("❌ Error: DJI_FOOTAGE_FOLDER_PATH not set in .env")
    else:
        process_files(SOURCE_FOLDER)