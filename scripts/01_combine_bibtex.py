import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.bwriter import BibTexWriter
from pathlib import Path
import os
from collections import Counter

def combine_bibtex_files(input_dir, output_dir):
    """
    Combine multiple bibtex files into two output files:
    one for entries with DOIs and one for entries without DOIs.
    
    Args:
        input_dir (str): Directory containing input .bib files
        output_dir (str): Directory for output bibtex files
    """
    # Dictionaries to store entries
    entries_with_doi = {}    # Using DOI as key
    entries_without_doi = {} # Using ID as key
    
    # Tracking variables for statistics
    total_initial_entries = 0
    entry_types = Counter()
    entries_by_file = {}
    
    # Process each .bib file in the input directory
    input_path = Path(input_dir)
    bib_files = sorted(input_path.glob("*.bib"))
    
    print("\nReading and processing files:")
    print("-" * 40)
    
    for bib_file in bib_files:
        try:
            # Create new parser for each file
            parser = BibTexParser(common_strings=True)
            
            with open(bib_file, 'r', encoding='utf-8') as bibtex_file:
                # Load and parse file
                content = bibtex_file.read()
                bib_database = bibtexparser.loads(content, parser)
                
                # Count entries in this file
                file_entries = len(bib_database.entries)
                entries_by_file[bib_file.name] = file_entries
                total_initial_entries += file_entries
                
                # Validate entry count matches @ count
                at_count = content.count('\n@')
                if at_count != file_entries:
                    print(f"WARNING: Count mismatch in {bib_file.name}")
                    print(f"  Parser found {file_entries} entries")
                    print(f"  @ symbol count: {at_count}")
                
                # Process each entry
                for entry in bib_database.entries:
                    # Track entry type
                    entry_type = entry.get('ENTRYTYPE', 'unknown')
                    entry_types[entry_type] += 1
                    
                    # Get DOI if it exists
                    doi = entry.get('doi', '').strip().upper()
                    entry_id = entry.get('ID', '')
                    
                    if doi:
                        entries_with_doi[doi] = entry
                    elif entry_id:
                        entries_without_doi[entry_id] = entry
                
                print(f"{bib_file.name}: {file_entries:,} entries")
                        
        except Exception as e:
            print(f"Error processing {bib_file}: {str(e)}")
            continue

    # Create output directory if needed
    os.makedirs(output_dir, exist_ok=True)
    
    # Create writer
    writer = BibTexWriter()
    writer.indent = '  '  # Indent entries
    writer.order_entries_by = ('author', 'year', 'title')
    
    # Write entries with DOIs
    out_db = bibtexparser.bibdatabase.BibDatabase()
    out_db.entries = list(entries_with_doi.values())
    doi_outfile = os.path.join(output_dir, 'unfiltered_doi.bib')
    with open(doi_outfile, 'w', encoding='utf-8') as out_file:
        out_file.write(writer.write(out_db))
    
    # Write entries without DOIs
    out_db = bibtexparser.bibdatabase.BibDatabase()
    out_db.entries = list(entries_without_doi.values())
    nodoi_outfile = os.path.join(output_dir, 'unfiltered_nodoi.bib')
    with open(nodoi_outfile, 'w', encoding='utf-8') as out_file:
        out_file.write(writer.write(out_db))
    
    # Print detailed summary
    print("\nEntry types found:")
    print("-" * 40)
    for entry_type, count in sorted(entry_types.items()):
        print(f"{entry_type}: {count:,}")
        
    print("\nProcessing summary:")
    print("-" * 40)
    print(f"Initial entries read: {total_initial_entries:,}")
    print(f"After deduplication:")
    print(f"  Entries with DOIs: {len(entries_with_doi):,}")
    print(f"  Entries without DOIs: {len(entries_without_doi):,}")
    total_unique = len(entries_with_doi) + len(entries_without_doi)
    print(f"  Total unique entries: {total_unique:,}")
    print(f"Duplicates removed: {total_initial_entries - total_unique:,}")
    
    print("\nOutput files:")
    print("-" * 40)
    print(f"Records with DOIs: {doi_outfile}")
    print(f"Records without DOIs: {nodoi_outfile}")

if __name__ == "__main__":
    # Define input and output paths
    input_dir = "WoS_exports"
    output_dir = "analysis"
    
    combine_bibtex_files(input_dir, output_dir)
