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


def find_all_folders_for_date(source_path, date):
    """
    Find ALL folders that exist for a given date [YYYY.MM.DD]
    Supports both formats:
    - [YYYY.MM.DD] #X description (new format)
    - [YYYY.MM.DD] description (legacy format without #X)
    
    Returns a list of tuples: [(folder_path, folder_number, description, has_number), ...]
    """
    source_dir = Path(source_path)
    display_date = date.replace('-', '.')
    prefix = f"[{display_date}]"
    
    folders = []
    # Pattern for new format: [YYYY.MM.DD] #X description
    pattern_with_number = re.compile(rf'\[{re.escape(display_date)}\]\s*#(\d+)\s+(.+)')
    # Pattern for legacy format: [YYYY.MM.DD] description (no #X)
    pattern_without_number = re.compile(rf'\[{re.escape(display_date)}\]\s+(.+)')
    
    for item in source_dir.iterdir():
        if item.is_dir() and item.name.startswith(prefix):
            # Try matching new format first
            match = pattern_with_number.match(item.name)
            if match:
                folder_num = int(match.group(1))
                description = match.group(2).strip()
                folders.append((item, folder_num, description, True))
            else:
                # Try legacy format
                match = pattern_without_number.match(item.name)
                if match:
                    description = match.group(1).strip()
                    # Legacy folders get assigned number 0 (will be handled specially)
                    folders.append((item, 0, description, False))
    
    # Sort: legacy folders (0) first, then by folder number
    folders.sort(key=lambda x: x[1])
    return folders


def get_next_folder_number(source_path, date):
    """
    Get the next available folder number for a given date
    Accounts for both numbered and legacy unnumbered folders
    """
    existing_folders = find_all_folders_for_date(source_path, date)
    if not existing_folders:
        return 1
    
    # Find highest numbered folder (skip legacy folders with number 0)
    numbered_folders = [f for f in existing_folders if f[3]]  # f[3] is has_number
    
    if not numbered_folders:
        # Only legacy folders exist, start numbering from 1
        return 1
    
    # Return highest folder number + 1
    return max(f[1] for f in numbered_folders) + 1


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
        
        existing_folders = find_all_folders_for_date(source_path, date)
        display_date = date.replace('-', '.')
        
        if existing_folders:
            print(f"   Found {len(existing_folders)} existing folder(s) for this date:")
            for i, (folder_path, folder_num, desc, has_number) in enumerate(existing_folders, 1):
                if has_number:
                    print(f"      {i}. {folder_path.name}")
                else:
                    print(f"      {i}. {folder_path.name} [LEGACY - no #X]")
            
            print(f"      {len(existing_folders) + 1}. Create NEW folder")
            
            choice = input(f"   Select option (1-{len(existing_folders) + 1}): ").strip()
            
            try:
                choice_idx = int(choice) - 1
                if 0 <= choice_idx < len(existing_folders):
                    # Use existing folder
                    folder_path, folder_num, description, has_number = existing_folders[choice_idx]
                    print(f"   Using: {folder_path.name}")
                    
                    if not has_number:
                        print(f"   ⚠️  WARNING: This is a legacy folder without #X numbering")
                else:
                    # Create new folder
                    folder_num = get_next_folder_number(source_path, date)
                    description = input(f"   Enter description for new folder: ").strip()
                    folder_name = f"[{display_date}] #{folder_num} {description}"
                    folder_path = Path(source_path) / folder_name
            except (ValueError, IndexError):
                print("   Invalid choice, creating new folder...")
                folder_num = get_next_folder_number(source_path, date)
                description = input(f"   Enter description for new folder: ").strip()
                folder_name = f"[{display_date}] #{folder_num} {description}"
                folder_path = Path(source_path) / folder_name
        else:
            # No existing folders, create first one
            folder_num = 1
            description = input(f"   Enter description for {display_date}: ").strip()
            folder_name = f"[{display_date}] #{folder_num} {description}"
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