import anthropic
import os
import time
import json
import argparse
from pathlib import Path
import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.customization import convert_to_unicode
import base64
from typing import List, Dict, Optional
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='Extract flower visitor information from PDFs')
    parser.add_argument('--bibtex', default='export_20241224.bib',
                       help='Input BibTeX file')
    parser.add_argument('--pdf-dir', default='pdfs/export_20241224',
                       help='Directory containing PDF files')
    parser.add_argument('--output', default='analysis/flower_visitor_records.json',
                       help='Output JSON file')
    parser.add_argument('--use-batches', action='store_true',
                       help='Use the Batches API for processing')
    parser.add_argument('--test', action='store_true',
                       help='Run in test mode (process only 3 PDFs)')
    return parser.parse_args()

def load_bibtex(filename: str) -> Dict[str, Dict]:
    """Load BibTeX and create DOI to entry mapping"""
    parser = BibTexParser()
    parser.customization = convert_to_unicode
    
    with open(filename, 'r', encoding='utf-8') as bibtex_file:
        bdb = bibtexparser.load(bibtex_file, parser=parser)
    
    # Create DOI to entry mapping
    return {entry.get('doi', ''): entry for entry in bdb.entries if entry.get('doi')}

def load_pdf(filepath: str) -> Optional[str]:
    """Load and encode PDF file"""
    try:
        with open(filepath, 'rb') as f:
            return base64.b64encode(f.read()).decode('utf-8')
    except Exception as e:
        print(f"Error loading PDF {filepath}: {e}")
        return None

def create_extraction_prompt(doi: str) -> str:
    """Create prompt for extracting flower visitor information"""
    return """Analyze this scientific paper for empirical observations of flower visitors. 
    
Your task is to:
1. Determine if the paper contains any empirical observations of flower visitors
2. If yes, extract all records of flower visitors, where each record represents observations of one plant species in one country

For each record, extract:
- Country where observations were made
- Plant species (most precise taxonomic level used)
- Method of observation (briefly)
- Time of observation (day/night/both)
- List of all flower visitors observed

Provide your analysis in this exact JSON format:
{
    "has_visitor_data": true/false,
    "records": [
        {
            "doi": "paper_doi",
            "country": "country_name",
            "plant_species": "species_name",
            "method": "brief_method",
            "observation_time": "day/night/both",
            "visitors": ["visitor1", "visitor2", ...]
        },
        ...
    ]
}

If there are no empirical observations, return has_visitor_data as false with an empty records list."""

def extract_json_from_response(text: str) -> Dict:
    """Extract and parse JSON from Claude's response"""
    try:
        # Look for JSON structure in the response
        import re
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception as e:
        print(f"Error parsing JSON response: {e}")
    return None

def process_pdf_direct(client: Anthropic, pdf_data: str, doi: str) -> Dict:
    """Process a single PDF using direct API calls"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=4096,
                temperature=0,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_data
                            }
                        },
                        {
                            "type": "text",
                            "text": create_extraction_prompt(doi)
                        }
                    ]
                }]
            )
            result = extract_json_from_response(response.content[0].text)
            if result:
                # Ensure DOI is added to each record
                if result.get('records'):
                    for record in result['records']:
                        record['doi'] = doi
                return result
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"Failed to process PDF after {max_retries} attempts: {e}")
                return None
            time.sleep(2 ** attempt)
    return None

def process_pdfs_batch(client: Anthropic, pdf_list: List[tuple]) -> Dict[str, Dict]:
    """Process multiple PDFs using Batches API"""
    requests = []
    
    for doi, pdf_data in pdf_list:
        requests.append(Request(
            custom_id=doi,
            params=MessageCreateParamsNonStreaming(
                model="claude-3-5-sonnet-20241022",
                max_tokens=4096,
                temperature=0,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_data
                            }
                        },
                        {
                            "type": "text",
                            "text": create_extraction_prompt(doi)
                        }
                    ]
                }]
            )
        ))

    # Create and monitor batch
    message_batch = client.messages.batches.create(requests=requests)
    while message_batch.processing_status == "in_progress":
        print("Waiting for batch processing...")
        time.sleep(30)
        message_batch = client.messages.batches.retrieve(message_batch.id)

    results = {}
    if message_batch.processing_status == "ended":
        for result in client.messages.batches.results(message_batch.id):
            if result.result.type == "succeeded":
                doi = result.custom_id
                extracted = extract_json_from_response(result.result.message.content[0].text)
                if extracted:
                    results[doi] = extracted
    
    return results

def main():
    args = parse_args()
    
    # Check for API key
    if not os.getenv('ANTHROPIC_API_KEY'):
        raise ValueError("Please set ANTHROPIC_API_KEY environment variable")
    
    client = Anthropic()
    
    # Load DOI to BibTeX mapping
    doi_to_entry = load_bibtex(args.bibtex)
    print(f"Loaded {len(doi_to_entry)} entries from BibTeX")
    
    # Load existing results if any
    results = {}
    if os.path.exists(args.output):
        with open(args.output, 'r') as f:
            results = json.load(f)
    
    # Process PDFs
    pdf_dir = Path(args.pdf_dir)
    pdf_files = list(pdf_dir.glob('**/*.pdf'))
    if args.test:
        pdf_files = pdf_files[:3]
        print(f"Test mode: processing {len(pdf_files)} PDFs")
    
    # Create list of (doi, pdf_data) tuples for unprocessed PDFs
    to_process = []
    for pdf_path in pdf_files:
        # Try to match PDF to DOI
        for doi, entry in doi_to_entry.items():
            if doi and doi not in results:
                pdf_data = load_pdf(pdf_path)
                if pdf_data:
                    to_process.append((doi, pdf_data))
                    break
    
    if to_process:
        if args.use_batches:
            print(f"Processing {len(to_process)} PDFs using Batches API...")
            batch_results = process_pdfs_batch(client, to_process)
            results.update(batch_results)
        else:
            for doi, pdf_data in to_process:
                print(f"\nProcessing PDF for DOI: {doi}")
                result = process_pdf_direct(client, pdf_data, doi)
                if result:
                    results[doi] = result
                # Save after each PDF
                with open(args.output, 'w') as f:
                    json.dump(results, f, indent=2)
                time.sleep(1)
    
    # Save final results
    with open(args.output, 'w') as f:
        json.dump(results, f, indent=2)
    
    # Print summary
    total_records = sum(len(r.get('records', [])) for r in results.values())
    print(f"\nProcessing complete!")
    print(f"Total PDFs processed: {len(results)}")
    print(f"Total flower visitor records extracted: {total_records}")
    print(f"Results saved to {args.output}")

if __name__ == "__main__":
    main()
