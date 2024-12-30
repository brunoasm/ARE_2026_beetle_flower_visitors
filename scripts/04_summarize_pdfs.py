# Import necessary libraries
import anthropic
from anthropic import Anthropic
import os, re
import tempfile
import subprocess
import time
import json
import argparse
from pathlib import Path
import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.customization import convert_to_unicode
import base64
import pikepdf
import io
import shutil
from typing import List, Dict, Optional
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

def parse_args():
    """Parse command line arguments with sensible defaults for project structure"""
    project_root = Path.cwd()
    
    parser = argparse.ArgumentParser(description='Extract flower visitor information from PDFs')
    parser.add_argument('--pdf-collections', 
                       default=str(project_root / 'pdfs'),
                       help='Root directory containing PDF collections')
    parser.add_argument('--output', 
                       default=str(project_root / 'analysis' / 'flower_visitor_records.json'),
                       help='Output JSON file')
    parser.add_argument('--use-batches', action='store_true',
                       help='Use the Batches API for processing')
    parser.add_argument('--test', action='store_true',
                       help='Run in test mode (process only 3 PDFs)')
    parser.add_argument('--target-dpi', type=int, default=72,
                       help='Target DPI for PDF image compression (default: 72)')
    return parser.parse_args()


def optimize_pdf(pdf_data: bytes) -> bytes:
    """
    Optimize PDF file size using Ghostscript with settings optimized for API submission.
    Uses 72 DPI (screen resolution) and 60% JPEG quality since we don't need print quality
    for machine learning analysis.
    
    Args:
        pdf_data: Raw PDF bytes to optimize
    
    Returns:
        Optimized PDF as bytes, or original if optimization fails
    """
    # Verify Ghostscript is available
    try:
        gs_path = shutil.which('gs')
        if not gs_path:
            raise ValueError("Ghostscript not found. Please install Ghostscript.")
    except Exception as e:
        print(f"Error checking for Ghostscript: {e}")
        return pdf_data

    # Create temporary directory for processing
    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = Path(temp_dir) / "input.pdf"
        output_path = Path(temp_dir) / "output.pdf"
        
        # Write input PDF to temporary file
        with open(input_path, 'wb') as f:
            f.write(pdf_data)
        
        # Configure Ghostscript commands for API-appropriate optimization
        gs_command = [
            'gs',
            '-sDEVICE=pdfwrite',
            '-dNOPAUSE',
            '-dQUIET',
            '-dBATCH',
            '-dPDFSETTINGS=/screen',     # Base profile for screen viewing
            '-dCompatibilityLevel=1.4',   # Modern but widely compatible PDF version
            # Image settings
            '-dDownsampleColorImages=true',
            '-dDownsampleGrayImages=true',
            '-dDownsampleMonoImages=true',
            '-dColorImageResolution=72',   # Screen resolution DPI
            '-dGrayImageResolution=72',
            '-dMonoImageResolution=72',
            # JPEG compression
            '-dJPEGQ=60',                 # 60% JPEG quality
            # General compression
            '-dCompressPages=true',
            '-dUseFlateCompression=true',
            '-dCompressFonts=true',
            '-dEmbedAllFonts=true',
            '-dSubsetFonts=true',
            # Output configuration
            '-sOutputFile=' + str(output_path),
            str(input_path)
        ]
        
        try:
            # Run Ghostscript with a reasonable timeout
            subprocess.run(gs_command, check=True, capture_output=True, timeout=300)
            
            # Report size change
            output_size = output_path.stat().st_size / (1024 * 1024)  # MB
            input_size = input_path.stat().st_size / (1024 * 1024)    # MB
            
            # Read and return optimized PDF
            with open(output_path, 'rb') as f:
                optimized_data = f.read()
                
            # Check if we're under API limit
            if len(optimized_data) > 32 * 1024 * 1024:
                raise ValueError(f"Optimized PDF size ({output_size:.1f}MB) exceeds 32MB limit")
                
            return optimized_data
                
        except subprocess.CalledProcessError as e:
            print(f"Ghostscript error: {e.stderr.decode()}")
            return pdf_data
        except Exception as e:
            print(f"Optimization error: {e}")
            return pdf_data
  
def find_bibtex_files(root_dir: Path) -> List[Path]:
    """Find all BibTeX files within the given directory tree"""
    return list(root_dir.glob('**/*.bib'))

def resolve_pdf_path(bib_file_path: Path, pdf_relative_path: str) -> Optional[Path]:
    """Resolve the full PDF path given the BibTeX file location and relative path"""
    try:
        parts = pdf_relative_path.split(':')
        if len(parts) >= 2:
            relative_path = parts[1]
            return (bib_file_path.parent / relative_path).resolve()
    except Exception as e:
        print(f"Error resolving PDF path: {e}")
    return None

def load_bibtex_entries(root_dir: Path) -> Dict[str, Dict]:
    """Load all BibTeX files and create DOI to entry mapping with resolved PDF paths"""
    parser = BibTexParser()
    parser.customization = convert_to_unicode
    
    doi_to_entry = {}
    
    bib_files = find_bibtex_files(root_dir)
    print(f"Found {len(bib_files)} BibTeX files")
    
    for bib_file in bib_files:
        print(f"Processing BibTeX file: {bib_file}")
        try:
            with open(bib_file, 'r', encoding='utf-8') as f:
                bdb = bibtexparser.load(f, parser=parser)
            
            for entry in bdb.entries:
                doi = entry.get('doi')
                if doi:
                    if 'file' in entry:
                        pdf_path = resolve_pdf_path(bib_file, entry['file'])
                        if pdf_path:
                            entry['resolved_pdf_path'] = str(pdf_path)
                    doi_to_entry[doi] = entry
        except Exception as e:
            print(f"Error processing BibTeX file {bib_file}: {e}")
    
    return doi_to_entry

def load_pdf(filepath: str, target_dpi: int = 72) -> Optional[str]:
    """Load, optimize, and base64 encode a PDF file"""
    try:
        with open(filepath, 'rb') as f:
            pdf_data = f.read()
            
        # Optimize PDF
        optimized_data = optimize_pdf(pdf_data)
        
        # Report optimization results
        original_size = len(pdf_data) / 1024 / 1024  # MB
        optimized_size = len(optimized_data) / 1024 / 1024  # MB
        print(f"PDF optimized from {original_size:.1f}MB to {optimized_size:.1f}MB "
              f"({(optimized_size/original_size)*100:.1f}% of original)")
        
        return base64.b64encode(optimized_data).decode('utf-8')
        
    except ValueError as e:
        raise
    except Exception as e:
        print(f"Error processing PDF {filepath}: {e}")
        return None

def create_extraction_prompt(doi: str) -> str:
    """Create prompt for extracting flower visitor information"""
    prompt = '''Your objective is to carefully analyze this paper and extract empirical observations of flower visitors.

Please follow these steps to analyze the paper and extract the required information:

1. Determine if the paper contains any empirical observations of flower visitors.
2. If empirical observations are present, extract all records of flower visitors. Each record should represent observations of one plant species in one locality.

For each record, extract the following information:
- Location: country, state/province (administrative area level 2), and locality
- Plant species (use the most precise taxonomic level provided in the paper)
- Method of observation (brief description)
- Time of observation (list: day/night/dusk/dawn)
- List of all flower visitors observed (use exact names as they appear in the publication)

Additionally, for each record, determine:
- Whether any of the flower visitors reported is a beetle (Coleoptera)
- Whether any of the effective pollinators reported is a beetle (Coleoptera)
- Whether the observations are unbiased (must have been made during multiple times during the day and night, allowing for observations of flower visitors of multiple sizes and behaviors)
- Whether the main method was direct observation or direct sampling with a sweep net

Important considerations:
- Only include primary observations from the paper
- If a record involves more than one plant species or country, separate it into multiple records
- Do not add any variables to the output that are not explicitly listed in the example JSON structure

Before providing your final output, please wrap your analysis in <paper_analysis> tags. In this analysis:

a. Identify and quote relevant sections of the paper that contain empirical observations of flower visitors.
b. List out each plant species mentioned in these observations.
c. For each plant species, extract the required information (location, method, time, visitors, etc.).
d. Assess whether any visitors or pollinators are beetles by listing out all visitors/pollinators and marking those that are beetles.
e. Evaluate whether the methods are unbiased by listing out the observation times and methods used.

This analysis ensures a thorough interpretation of the data. It's okay for this section to be quite long, as it may involve listing out multiple plant species and their associated information.

After your analysis, provide the final output in the following JSON format, wrapped in <output> tags:

<output>
{
  "doi": "paper_doi",
  "has_visitor_data": true/false,
  "has_visitor_notes": "brief explanation of evidence supporting the assessment",
  "records": [
    {
      "country": "country_name",
      "state_province": "state_name",
      "locality": "specific_location",
      "plant_species": "species_name",
      "method": "brief_method_description",
      "observation_time": ["time1", "time2", ...],
      "visitors": ["visitor1", "visitor2", ...],
      "beetle_visitors": true/false,
      "beetle_pollinators": true/false,
      "methods_unbiased": true/false,
      "methods_biased_reasoning": "one-sentence explanation for unbiased assessment",
      "methods_direct": true/false
    }
  ]
}
</output>

Remember to be comprehensive in your analysis while avoiding false positives. Ensure that your output strictly adheres to the provided JSON structure without adding any additional variables.'''
    return prompt

def extract_json_from_response(text: str) -> Dict:
    """Extract JSON from XML output tags"""

    # Find content between <output> tags
    match = re.search(r'<output>\s*(\{.*?\})\s*</output>', text, re.DOTALL)
    if match:
        json_str = match.group(1)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            print(f"Failed to parse JSON: {json_str}")
            return None
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
                system="You are a scientific research assistant specializing in analyzing papers about plant-pollinator interactions. Your task is to carefully analyze a scientific paper and extract empirical observations of flower visitors.",
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_data
                            },
                            "cache_control": {"type": "ephemeral"}
                        },
                        {
                            "type": "text",
                            "text": create_extraction_prompt(doi)
                        }
                    ]
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "<extraction_process>"
                        }
                    ]
                }
                ]
            )
            result = extract_json_from_response(response.content[0].text)
            print(response.content[0].text)
            if result:
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
    
    if not os.getenv('ANTHROPIC_API_KEY'):
        raise ValueError("Please set ANTHROPIC_API_KEY environment variable")
    
    client = Anthropic()
    
    pdf_collections_dir = Path(args.pdf_collections)
    doi_to_entry = load_bibtex_entries(pdf_collections_dir)
    print(f"Loaded {len(doi_to_entry)} entries from BibTeX files")
    
    results = {}
    if os.path.exists(args.output):
        with open(args.output, 'r') as f:
            results = json.load(f)
    
    to_process = []
    
    for doi, entry in doi_to_entry.items():
        if doi and doi not in results:
            if 'resolved_pdf_path' in entry:
                pdf_path = Path(entry['resolved_pdf_path'])
                if pdf_path.exists():
                    print(f"Found PDF for DOI {doi}: {pdf_path}")
                    try:
                        pdf_data = load_pdf(str(pdf_path), target_dpi=args.target_dpi)
                        if pdf_data:
                            to_process.append((doi, pdf_data))
                    except ValueError as e:
                        print(f"Skipping {pdf_path}: {str(e)}")
                    except Exception as e:
                        print(f"Error loading PDF {pdf_path}: {e}")
                else:
                    print(f"PDF file not found at resolved path: {pdf_path}")
            else:
                print(f"No PDF path found for DOI {doi}")

    if args.test:
        to_process = to_process[:10]
        print(f"Test mode: processing {len(to_process)} PDFs")
    
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
                with open(args.output, 'w') as f:
                    json.dump(results, f, indent=2)
                time.sleep(1)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    
    with open(args.output, 'w') as f:
        json.dump(results, f, indent=2)
    
    # Print summary statistics
    total_records = sum(len(r.get('records', [])) for r in results.values())
    records_with_beetles = sum(
        1 for r in results.values() 
        for record in r.get('records', []) 
        if record.get('beetle_visitors') or record.get('beetle_pollinators')
    )
    
    print(f"\nProcessing complete!")
    print(f"Total PDFs processed: {len(results)}")
    print(f"Total flower visitor records extracted: {total_records}")
    print(f"Records involving beetles: {records_with_beetles}")
    print(f"Results saved to {args.output}")

if __name__ == "__main__":
    main() 
