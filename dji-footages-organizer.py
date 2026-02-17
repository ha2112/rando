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
    Supports: MP4, WAV, DNG, JPG
    """
    pattern = r'DJI_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})_(\d{4})_D\.(\w+)'
    match = re.match(pattern, filename, re.IGNORECASE)
    
    if match:
        year, month, day, hour, minute, second, sequence, ext = match.groups()
        ext_lower = ext.lower()
        
        # Determine file type
        if ext_lower in ['mp4', 'mov']:
            file_type = 'video'
        elif ext_lower == 'wav':
            file_type = 'audio'
        elif ext_lower == 'dng':
            file_type = 'raw_image'
        elif ext_lower in ['jpg', 'jpeg']:
            file_type = 'image'
        else:
            file_type = 'unknown'
        
        return {
            'year': year,
            'short_year': year[-2:], # last two digits
            'month': month,
            'day': day,
            'hour': hour,
            'minute': minute,
            'second': second,
            'sequence': sequence,
            'extension': ext_lower,
            'file_type': file_type,
            'date': f"{year}-{month}-{day}",
            'timestamp': f"{year}{month}{day}{hour}{minute}{second}"
        }
    return None


def create_new_filename(parsed_info, description, counter, file_type_prefix):
    """
    Creates filename with type prefix before the number:
    - Videos: [dd-mm-yy_hh-mm-ss]_title_in_lowercase_[VID]_#X.ext
    - Audio:  [dd-mm-yy_hh-mm-ss]_title_in_lowercase_[AU]_#X.ext
    - Images: [dd-mm-yy_hh-mm-ss]_title_in_lowercase_[IMG]_#X.ext
    """
    # Format components
    date_part = f"{parsed_info['day']}-{parsed_info['month']}-{parsed_info['short_year']}"
    time_part = f"{parsed_info['hour']}-{parsed_info['minute']}-{parsed_info['second']}"
    
    # Process title: lowercase and replace spaces with underscores for cleanliness
    clean_title = description.lower().replace(" ", "_")
    
    ext = parsed_info['extension']
    
    return f"[{date_part}_{time_part}]_{clean_title}_[{file_type_prefix}]_#{counter}.{ext}"


def get_file_type_prefix(file_type):
    """
    Returns the appropriate prefix for each file type
    """
    prefix_map = {
        'video': 'VID',
        'audio': 'AU',
        'raw_image': 'IMG',
        'image': 'IMG'
    }
    return prefix_map.get(file_type, 'FILE')


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


def get_highest_part_number(folder_path, file_type_prefix):
    """
    Scans folder to find the current highest #X suffix for a specific file type.
    Different file types (VID, AU, IMG) have independent numbering.
    """
    if not folder_path.exists():
        return 0
    
    highest = 0
    # Regex looks for [PREFIX]_#X.ext pattern (updated to match new format)
    pattern = re.compile(rf'\[{file_type_prefix}\]_#(\d+)\.\w+$')
    
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


def get_file_summary(timestamp_groups):
    """
    Generate summary of files to be processed
    Returns: dict with counts of videos, images, and total items
    """
    video_count = 0
    image_count = 0
    
    for timestamp, files in timestamp_groups.items():
        has_video = any(f['parsed']['file_type'] in ['video', 'audio'] for f in files)
        has_image = any(f['parsed']['file_type'] in ['raw_image', 'image'] for f in files)
        
        if has_video:
            video_count += 1
        if has_image:
            image_count += 1
    
    return {
        'videos': video_count,
        'images': image_count,
        'total': video_count + image_count
    }


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
        
        # Group by timestamp to show summary
        timestamp_groups = defaultdict(list)
        for f_info in files:
            timestamp_groups[f_info['parsed']['timestamp']].append(f_info)
        
        # Show file summary
        summary = get_file_summary(timestamp_groups)
        print(f"   Found: {summary['videos']} video(s), {summary['images']} image(s) ({summary['total']} total items)")
        
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
        
        # Initialize separate counters for each file type
        video_counter = get_highest_part_number(folder_path, 'VID') + 1
        audio_counter = get_highest_part_number(folder_path, 'AU') + 1
        image_counter = get_highest_part_number(folder_path, 'IMG') + 1
        
        # Process each timestamp group
        for timestamp in sorted(timestamp_groups.keys()):
            group_files = timestamp_groups[timestamp]
            
            # Determine what type of item this is and which counter to use
            file_types = {f['parsed']['file_type'] for f in group_files}
            
            if 'video' in file_types or 'audio' in file_types:
                item_type = "🎥 Video"
                current_counter = video_counter
            elif 'raw_image' in file_types or 'image' in file_types:
                item_type = "📷 Image"
                current_counter = image_counter
            else:
                item_type = "📄 File"
                current_counter = 1
            
            # Process all files in this timestamp group
            for file_info in group_files:
                old_path = file_info['original_path']
                file_type = file_info['parsed']['file_type']
                file_type_prefix = get_file_type_prefix(file_type)
                
                # Use the appropriate counter based on file type
                if file_type in ['video', 'audio']:
                    counter_to_use = video_counter
                elif file_type in ['raw_image', 'image']:
                    counter_to_use = image_counter
                else:
                    counter_to_use = 1
                
                new_filename = create_new_filename(
                    file_info['parsed'], 
                    description, 
                    counter_to_use,
                    file_type_prefix
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
            
            # Show what was processed and increment the appropriate counter
            if not DRY_RUN:
                print(f"      {item_type} #{current_counter} processed")
            
            # Increment the appropriate counter
            if 'video' in file_types or 'audio' in file_types:
                video_counter += 1
            elif 'raw_image' in file_types or 'image' in file_types:
                image_counter += 1

if __name__ == "__main__":
    if not SOURCE_FOLDER:
        print("❌ Error: DJI_FOOTAGE_FOLDER_PATH not set in .env")
    else:
        process_files(SOURCE_FOLDER)