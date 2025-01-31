import os
import shutil
from pathlib import Path
from pybtex.database import BibliographyData, Entry
from pybtex.database.input import bibtex
from pybtex.database.output import bibtex as bibtex_writer

def clean_bibtex_file(bib_path):
    """Removes duplicate annote fields from the bibtex file."""
    cleaned_lines = []
    seen_annote = False
    with open(bib_path, 'r', encoding='utf-8') as f:
        for line in f:
            stripped_line = line.strip()
            if stripped_line.startswith("annote ="):
                if seen_annote:
                    continue  # Skip duplicate annote field
                seen_annote = True
            else:
                if stripped_line.startswith("@"):  # New entry
                    seen_annote = False
            cleaned_lines.append(line)

    cleaned_path = bib_path.with_suffix('.cleaned.bib')
    with open(cleaned_path, 'w', encoding='utf-8') as f:
        f.writelines(cleaned_lines)
    return cleaned_path

def parse_file_field(file_field):
    """Parse the file field to extract PDF paths."""
    if not file_field:
        return []

    # Remove outer braces if present
    if file_field.startswith('{') and file_field.endswith('}'):
        file_field = file_field[1:-1]

    files = []
    for entry in file_field.split(';'):
        parts = entry.strip().split(':')
        if len(parts) >= 3:
            label = parts[0].strip()
            path = parts[1].strip()
            filetype = parts[2].strip().lower()
            if filetype == 'application/pdf' and path.lower().endswith('.pdf'):
                files.append(path)
    return files

def find_file_with_glob(files_dir, partial_path):
    """Find file using glob if exact path not found."""
    try:
        files_dir = Path(files_dir)
        partial_path = Path(partial_path)
        
        # Try exact path first
        exact_path = files_dir.parent / partial_path
        if exact_path.exists():
            return exact_path

        # Try glob search
        parent_dir = partial_path.parent
        filename = partial_path.name
        search_dir = files_dir.parent / parent_dir
        
        if search_dir.exists():
            filename_start = ''.join(c for c in filename[:3] if ord(c) < 128)
            pattern = f"{filename_start}*.pdf"
            matches = list(search_dir.glob(pattern))
            
            if matches:
                if len(matches) > 1:
                    print(f"⚠️ Multiple matches found for {filename}:")
                    for match in matches:
                        print(f"  - {match.relative_to(files_dir.parent)}")
                return matches[0]
    except Exception as e:
        print(f"Error in glob search for {partial_path}: {e}")
    return None

def process_bibliography(bib_path, files_dir):
    """Process bibliography and organize files."""
    # Clean and parse bibliography
    cleaned_path = clean_bibtex_file(bib_path)
    parser = bibtex.Parser()
    bib_data = parser.parse_file(cleaned_path)
    
    # Create new bibliography for modified entries
    new_bib_data = BibliographyData()
    
    # Prepare directories
    files_dir = Path(files_dir).resolve()
    files_backup_dir = files_dir.parent / "files_backup"
    files_kept_dir = files_dir.parent / "files_kept"
    
    files_backup_dir.mkdir(exist_ok=True)
    files_kept_dir.mkdir(exist_ok=True)
    
    # Track statistics
    stats = {'total': 0, 'kept': 0, 'not_found': 0}
    processed_files = set()
    
    # Process each entry
    for key, entry in bib_data.entries.items():
        # Create a new entry with the same type and fields
        new_entry = Entry(entry.type)
        new_entry.fields.update(entry.fields)
        new_entry.persons.update(entry.persons)
        
        if 'file' in entry.fields:
            stats['total'] += 1
            pdf_paths = parse_file_field(entry.fields['file'])
            
            if pdf_paths:
                pdf_path = pdf_paths[0]
                source_path = files_dir.parent / pdf_path.lstrip('/')
                new_filename = f"{key}.pdf"
                target_path = files_kept_dir / new_filename
                
                if source_path.exists():
                    shutil.copy2(source_path, target_path)
                    processed_files.add(str(source_path))
                    stats['kept'] += 1
                else:
                    found_path = find_file_with_glob(files_dir, pdf_path)
                    if found_path:
                        print(f"🔍 Found alternative: {found_path} -> {target_path}")
                        shutil.copy2(found_path, target_path)
                        processed_files.add(str(found_path))
                        stats['kept'] += 1
                    else:
                        print(f"❌ Not found: {pdf_path}")
                        stats['not_found'] += 1
                        
                # Update file field with new path
                if stats['kept'] > stats['not_found']:
                    new_entry.fields['file'] = f"PDF:files_kept/{new_filename}:application/pdf"
        
        # Add entry to new bibliography
        new_bib_data.entries[key] = new_entry
    
    # Move remaining files to backup
    backup_count = 0
    for root, _, files in os.walk(files_dir):
        for filename in files:
            if filename.startswith('.DS_Store'):
                continue
            file_path = Path(root) / filename
            if str(file_path) not in processed_files:
                relative_path = file_path.relative_to(files_dir)
                backup_path = files_backup_dir / relative_path
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(file_path), str(backup_path))
                backup_count += 1
    
    # Clean up temporary file
    os.unlink(cleaned_path)
    
    return new_bib_data, stats, backup_count

def main():
    bib_path = Path('pdfs/export_20250131/export_20250131.bib')
    files_dir = Path('pdfs/export_20250131/files')

    # Verify paths exist
    if not bib_path.exists():
        print(f"Error: Bibtex file not found at {bib_path}")
        return
    if not files_dir.exists():
        print(f"Error: Files directory not found at {files_dir}")
        return

    # Create backup
    backup_path = bib_path.with_suffix('.bib.backup')
    print(f"Creating backup at {backup_path}")
    shutil.copy2(bib_path, backup_path)

    print("Processing bibtex file...")
    new_bib_data, stats, backup_count = process_bibliography(bib_path, files_dir)

    # Write updated bibliography
    updated_bib_path = bib_path.with_suffix('.updated.bib')
    writer = bibtex_writer.Writer()
    with open(updated_bib_path, 'w', encoding='utf-8') as f:
        writer.write_stream(new_bib_data, f)

    print("\n📊 Summary:")
    print(f"🔹 Total files processed: {stats['total']}")
    print(f"✅ Files kept: {stats['kept']}")
    print(f"❌ Files not found: {stats['not_found']}")
    print(f"📦 Files moved to backup: {backup_count}")
    print(f"\nUpdated bibtex file saved to: {updated_bib_path}")

if __name__ == '__main__':
    main()
