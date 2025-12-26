import os
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime

# ============================================================================
# CONFIGURATION
# ============================================================================

# Set to True to see what would happen without actually renaming files
DRY_RUN = True
input_for_dry_run = input("Dry run(theoretical run): y/n\n")
if input_for_dry_run.lower() == "n":
    DRY_RUN = False
else:
    DRY_RUN = True

# Source folder containing organized DJI footage
from dotenv import load_dotenv
import os

load_dotenv()
SOURCE_FOLDER = os.getenv("DJI_FOOTAGE_FOLDER_PATH", "")

# ============================================================================
# MAIN SCRIPT
# ============================================================================

def parse_dji_filename(filename):
    """
    Parse DJI filename to extract date/time components.
    """
    pattern = r'DJI_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})_(\d{4})_D\.(\w+)'
    match = re.match(pattern, filename, re.IGNORECASE)
    
    if match:
        year, month, day, hour, minute, second, sequence, ext = match.groups()
        return {
            'year': year,
            'month': month,
            'day': day,
            'hour': hour,
            'minute': minute,
            'second': second,
            'sequence': sequence,
            'extension': ext.lower(),
            # Keeps ISO format for internal logic/sorting
            'date': f"{year}-{month}-{day}",
            'time': f"{hour}:{minute}:{second}",
            'timestamp': f"{year}{month}{day}{hour}{minute}{second}"
        }
    return None


def group_files_by_date(source_path):
    """
    Scan the source directory and group files by date.
    """
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
                    'original_name': file_path.name,
                    'parsed': parsed
                })
    
    return grouped_files


def format_file_size(size_bytes):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            if size_bytes == int(size_bytes):
                return f"{int(size_bytes)}{unit}"
            else:
                return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f}PB"


def create_new_filename(parsed_info, description, counter, file_size_bytes):
    """
    Create the new filename.
    UPDATED: Uses dots instead of slashes to prevent creating accidental subfolders.
    Format: [YYYY.MM.DD HH-MM-SS] {Description} Part {Counter} ({Size}).{ext}
    """
    # Changed '/' to '.' to match the requested folder style and prevent sub-directories
    date_str = f"{parsed_info['year']}.{parsed_info['month']}.{parsed_info['day']}"
    time_str = f"{parsed_info['hour']}-{parsed_info['minute']}-{parsed_info['second']}"
    ext = parsed_info['extension']
    size_str = format_file_size(file_size_bytes)
    return f"[{date_str} {time_str}] {description} Part {counter} ({size_str}).{ext}"


def find_existing_folder_for_date(source_path, date):
    """
    Check if a folder already exists for the given date.
    UPDATED: Looks for [YYYY.MM.DD] format.
    """
    source_dir = Path(source_path)
    
    # Convert YYYY-MM-DD to YYYY.MM.DD for the folder search
    display_date = date.replace('-', '.')
    pattern = f"[{display_date}]"
    
    for item in source_dir.iterdir():
        if item.is_dir() and item.name.startswith(pattern):
            description = item.name[len(pattern):].strip()
            return item, description
    
    return None, None


def get_highest_part_number(folder_path, description):
    if not folder_path.exists():
        return 0
    
    highest = 0
    # Updated regex to handle the new dot format in filenames as well
    pattern = re.compile(
        r'\[[\d\.:\ -]+\]\s+' + re.escape(description) + r'\s+Part\s+(\d+)(?:\s+\([^)]+\))?\.\w+', 
        re.IGNORECASE
    )
    
    for file_path in folder_path.iterdir():
        if file_path.is_file():
            match = pattern.match(file_path.name)
            if match:
                part_num = int(match.group(1))
                highest = max(highest, part_num)
    
    return highest


def process_files(source_path):
    print("=" * 70)
    print("DJI FOOTAGE ORGANIZER")
    print("=" * 70)
    print(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE MODE'}")
    print(f"Source: {source_path}")
    print("=" * 70)
    print()
    
    grouped_files = group_files_by_date(source_path)
    
    if not grouped_files:
        print("❌ No DJI files found.")
        return
    
    sorted_dates = sorted(grouped_files.keys())
    
    for date in sorted_dates:
        files = grouped_files[date]
        
        # Display current processing
        print(f"📁 Processing {len(files)} file(s) for date: {date}")
        
        existing_folder, existing_description = find_existing_folder_for_date(source_path, date)
        
        # Convert date to display format (YYYY.MM.DD)
        display_date = date.replace('-', '.')
        
        if existing_folder:
            print(f"📂 Found existing folder: {existing_folder.name}")
            use_existing = input(f"   Use existing folder? (Y/n): ").strip().lower()
            
            if use_existing in ['', 'y', 'yes']:
                description = existing_description
                folder_path = existing_folder
            else:
                description = input(f"\n✏️  Enter NEW description for {display_date}: ").strip()
                if not description: continue
                
                # UPDATED: Folder name uses dots
                folder_name = f"[{display_date}] {description}"
                folder_path = Path(source_path) / folder_name
                
                if not DRY_RUN:
                    folder_path.mkdir(exist_ok=True)
        else:
            description = input(f"\n✏️  Enter description for {display_date}: ").strip()
            if not description: continue
            
            # UPDATED: Folder name uses dots
            folder_name = f"[{display_date}] {description}"
            folder_path = Path(source_path) / folder_name
            
            if DRY_RUN:
                print(f"[DRY RUN] Would create folder: {folder_name}")
            else:
                folder_path.mkdir(exist_ok=True)
                print(f"✅ Created folder: {folder_name}")
        
        # Group by timestamp (sync audio/video)
        timestamp_groups = defaultdict(list)
        for file_info in files:
            timestamp_groups[file_info['parsed']['timestamp']].append(file_info)
        
        highest_existing = get_highest_part_number(folder_path, description)
        counter = highest_existing + 1
        
        sorted_timestamps = sorted(timestamp_groups.keys())
        
        for timestamp in sorted_timestamps:
            for file_info in timestamp_groups[timestamp]:
                old_path = file_info['original_path']
                file_size = old_path.stat().st_size
                
                new_filename = create_new_filename(
                    file_info['parsed'], 
                    description, 
                    counter,
                    file_size
                )
                new_path = folder_path / new_filename
                
                print(f"   → {new_filename}")
                
                if not DRY_RUN:
                    try:
                        old_path.rename(new_path)
                    except Exception as e:
                        print(f"   ❌ Error: {e}")
            counter += 1
        print("-" * 70)

if __name__ == "__main__":
    if not os.path.exists(SOURCE_FOLDER):
        print(f"❌ Error: Source folder not found: {SOURCE_FOLDER}")
    else:
        process_files(SOURCE_FOLDER)