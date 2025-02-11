import pandas as pd
from PyRTF import Document, Section, Table, Cell, Paragraph, TabPS, Row, StyleSheet
import re
from typing import Dict, List, Tuple
import requests
import json
from citeproc import Citation, CitationItem
from citeproc import CitationStylesStyle, CitationStylesBibliography
from citeproc import formatter
from citeproc.source.json import CiteProcJSON
import tempfile
import os
from pybtex.database.input import bibtex
from pybtex.database import BibliographyData, Entry
from pybtex.scanner import TokenRequired
from pybtex.exceptions import PybtexError

def download_csl_style() -> str:
    """
    Download the Annual Reviews CSL style file from GitHub.
    
    Returns:
        str: Path to the downloaded CSL file
    
    This function downloads the official Annual Reviews citation style and saves it
    temporarily. The file is automatically cleaned up when the script finishes.
    """
    url = "https://raw.githubusercontent.com/citation-style-language/styles/master/annual-reviews.csl"
    
    temp_dir = tempfile.gettempdir()
    csl_path = os.path.join(temp_dir, 'annual-reviews.csl')
    
    response = requests.get(url)
    response.raise_for_status()
    
    with open(csl_path, 'w', encoding='utf-8') as f:
        f.write(response.text)
    
    return csl_path

def read_csv_data(csv_path: str) -> Tuple[pd.DataFrame, set]:
    """
    Read and process the CSV file containing plant and beetle data.
    
    Args:
        csv_path (str): Path to the CSV file
    
    Returns:
        Tuple[pd.DataFrame, set]: Processed DataFrame and set of reference IDs
    """
    df = pd.read_csv(csv_path)
    all_refs = set()
    for refs in df['References'].str.split(','):
        if isinstance(refs, list):
            all_refs.update([ref.strip() for ref in refs])
    return df, all_refs

def parse_bibtex(bibtex_path: str, needed_refs: set) -> List[Dict]:
    """
    Parse BibTeX file using pybtex and convert to citeproc-json format.
    
    Args:
        bibtex_path (str): Path to the BibTeX file
        needed_refs (set): Set of reference IDs needed
    
    Returns:
        List[Dict]: List of references in citeproc-json format
    
    This function converts BibTeX entries to the format required by citeproc-py,
    handling special characters and complex author names properly.
    """
    parser = bibtex.Parser()
    try:
        bib_data = parser.parse_file(bibtex_path)
    except PybtexError as e:
        print(f"Warning: BibTeX parsing error: {e}")
        bib_data = BibliographyData()
    
    # Convert to citeproc-json format
    entries = []
    for key in bib_data.entries:
        if key in needed_refs:
            entry = bib_data.entries[key]
            
            # Convert authors to citeproc format
            authors = []
            for person in entry.persons.get('author', []):
                author = {
                    'family': ' '.join(person.last_names) if person.last_names else '',
                    'given': ' '.join(person.first_names) if person.first_names else ''
                }
                if person.middle_names:
                    author['given'] += ' ' + ' '.join(person.middle_names)
                authors.append(author)
            
            # Create citeproc-json entry
            csl_entry = {
                'id': key,
                'type': entry.type,
                'author': authors
            }
            
            # Map other fields
            field_mapping = {
                'journal': 'container-title',
                'number': 'issue',
                'pages': 'page',
                'year': 'issued'
            }
            
            for field, value in entry.fields.items():
                if field in field_mapping:
                    if field == 'year':
                        csl_entry[field_mapping[field]] = {'date-parts': [[value]]}
                    else:
                        csl_entry[field_mapping[field]] = value
                else:
                    csl_entry[field] = value
            
            entries.append(csl_entry)
    
    return entries

def setup_citation_processor(references: List[Dict], csl_path: str) -> CitationStylesBibliography:
    """
    Set up the citation processor with the Annual Reviews style.
    
    Args:
        references (List[Dict]): List of references in citeproc-json format
        csl_path (str): Path to the CSL style file
    
    Returns:
        CitationStylesBibliography: Configured citation processor
    """
    # Create a CiteProcJSON source from our references
    bib_source = CiteProcJSON(references)
    
    # Load the citation style
    bib_style = CitationStylesStyle(csl_path)
    
    # Create the bibliography object
    return CitationStylesBibliography(bib_style, bib_source, formatter.plain)

def create_rtf_document(df: pd.DataFrame, bibliography: CitationStylesBibliography, 
                       ref_ids: List[str]) -> Document:
    """
    Create RTF document with formatted table and references.
    
    Args:
        df (pd.DataFrame): Input data
        bibliography (CitationStylesBibliography): Citation processor
        ref_ids (List[str]): List of reference IDs in sorting order
    
    Returns:
        Document: RTF document object
    """
    doc = Document()
    ss = doc.StyleSheet
    section = Section()
    doc.Sections.append(section)
    
    # Add title
    section.append('Plant-Beetle Interactions Database\n\n')
    
    # Create table
    table = Table(TabPS.DEFAULT_WIDTH * 6)
    
    # Add header row
    header_row = Row()
    for col in df.columns:
        header_row.append(Cell(Paragraph(ss.ParagraphStyles.Heading1, col)))
    table.append(header_row)
    
    # Add data rows
    for _, row in df.iterrows():
        data_row = Row()
        for value in row:
            if pd.isna(value):
                cell_text = ''
            else:
                cell_text = str(value)
            data_row.append(Cell(Paragraph(cell_text)))
        table.append(data_row)
    
    section.append(table)
    
    # Add references section
    section.append('\n\nReferences\n\n')
    
    # Register all citations with the processor
    for ref_id in ref_ids:
        bibliography.register(Citation([CitationItem(ref_id)]))
    
    # Add formatted references
    for i, (ref_id, text) in enumerate(zip(ref_ids, bibliography.bibliography()), 1):
        ref_text = ''.join(str(part) for part in text)
        section.append(f"{i}. {ref_text}\n")
    
    return doc

def get_sorted_reference_ids(bibliography: CitationStylesBibliography, ref_ids: set) -> List[str]:
    """
    Sort reference IDs by first author's last name.
    
    Args:
        bibliography (CitationStylesBibliography): Citation processor
        ref_ids (set): Set of reference IDs
    
    Returns:
        List[str]: Sorted list of reference IDs
    """
    def get_first_author(ref_id):
        bibliography.register(Citation([CitationItem(ref_id)]))
        entry = bibliography.source.get(ref_id)
        if 'author' in entry:
            first_author = entry['author'][0]
            return first_author.get('family', '').lower()
        return ''
    
    return sorted(ref_ids, key=get_first_author)

def main():
    """
    Main function that coordinates the entire process of creating the formatted report.
    """
    csv_path = "tables/plant_family_table.csv"
    bibtex_path = "pdfs/export_20250131/export_20250131.updated.bib"
    output_path = "tables/draft_table_2.rtf"
    
    try:
        # Create output directory if it doesn't exist
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        print("Downloading citation style...")
        csl_path = download_csl_style()
        
        print("Reading input files...")
        df, needed_refs = read_csv_data(csv_path)
        references = parse_bibtex(bibtex_path, needed_refs)
        
        print("Setting up citation processor...")
        bibliography = setup_citation_processor(references, csl_path)
        
        print("Sorting references...")
        sorted_refs = get_sorted_reference_ids(bibliography, needed_refs)
        
        print("Generating RTF document...")
        doc = create_rtf_document(df, bibliography, sorted_refs)
        
        with open(output_path, 'wb') as output:
            doc.write(output)
        
        print(f"Report successfully generated: {output_path}")
        
    except requests.exceptions.RequestException as e:
        print(f"Error downloading CSL file: {e}")
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        raise  # Re-raise the exception to see the full traceback
    finally:
        if 'csl_path' in locals() and os.path.exists(csl_path):
            os.remove(csl_path)

if __name__ == "__main__":
    main()