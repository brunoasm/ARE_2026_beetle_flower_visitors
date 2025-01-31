import os
import shutil
from pathlib import Path
from pybtex.database.input import bibtex

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
                seen_annote = False
            cleaned_lines.append(line)
    
    cleaned_path = bib_path.with_suffix('.cleaned.bib')
    with open(cleaned_path, 'w', encoding='utf-8') as f:
        f.writelines(cleaned_lines)
    
    return cleaned_path

def parse_file_field(file_field):
    if not file_field:
        return []
    if file_field.startswith('{') and file_field.endswith('}'):
        file_field = file_field[1:-1]

    files = []
    for entry in file_field.split(';'):
        parts = entry.strip().split(':')
        if len(parts) == 3 and parts[2].lower() == 'application/pdf':
            files.append(parts[1].strip())
    return files

def get_pdfs_to_keep(bib_path):
    """Parses the cleaned bibtex file to extract PDF paths."""
    bib_path = clean_bibtex_file(bib_path)  # Pre-clean before parsing
    parser = bibtex.Parser()
    bib_data = parser.parse_file(bib_path)
    pdfs_to_keep = {}

    for key, entry in bib_data.entries.items():
        if 'file' in entry.fields:
            pdf_paths = parse_file_field(entry.fields['file'])
            if pdf_paths:
                pdfs_to_keep[key] = pdf_paths[0]

    return pdfs_to_keep

def organize_files(files_dir, pdfs_to_keep):
    files_dir = Path(files_dir).resolve()
    files_backup_dir = files_dir.parent / "files_backup"
    files_backup_dir.mkdir(exist_ok=True)

    # Normalize paths relative to export_20250131
    kept_paths = {str(Path("files") / Path(pdf_path).relative_to("files")) for pdf_path in pdfs_to_keep.values()}

    print("\nPDFs to keep (first 5):")
    for path in list(kept_paths)[:5]:
        print(f"  {path}")

    stats = {'total': 0, 'kept': 0, 'moved': 0}

    for root, _, files in os.walk(files_dir):
        for filename in files:
            if filename.startswith('.DS_Store'):
                continue

            file_path = Path(root) / filename
            relative_path = file_path.relative_to(files_dir)
            resolved_path = str(Path("files") / relative_path)

            stats['total'] += 1

            if resolved_path in kept_paths:
                print(f"✅ Keeping: {file_path}")
                stats['kept'] += 1
            else:
                backup_path = files_backup_dir / relative_path
                backup_path.parent.mkdir(parents=True, exist_ok=True)

                print(f"📦 Moving: {file_path} -> {backup_path}")
                shutil.move(str(file_path), str(backup_path))
                stats['moved'] += 1

    return stats

def main():
    bib_path = Path('pdfs/export_20250131/export_20250131.bib')
    files_dir = Path('pdfs/export_20250131/files')

    backup_path = bib_path.with_suffix('.bib.backup')
    print(f"Creating backup at {backup_path}")
    shutil.copy2(bib_path, backup_path)

    print("Processing bibtex file...")
    pdfs_to_keep = get_pdfs_to_keep(bib_path)

    if not pdfs_to_keep:
        print("⚠️ Warning: No PDFs found in bibtex file!")
        return

    print(f"\nFound {len(pdfs_to_keep)} entries with PDFs")

    stats = organize_files(files_dir, pdfs_to_keep)

    print("\n📊 Summary:")
    print(f"🔹 Total files: {stats['total']}")
    print(f"✅ Kept: {stats['kept']}")
    print(f"📦 Moved: {stats['moved']}")

if __name__ == '__main__':
    main()

