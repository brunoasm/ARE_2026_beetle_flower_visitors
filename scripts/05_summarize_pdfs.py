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
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional, Tuple
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

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
    parser.add_argument('--cache-dir',
                       default=str(project_root / 'optimized_pdfs'),
                       help='Directory for caching optimized PDFs')
    parser.add_argument('--use-batches', action='store_true',
                       help='Use the Batches API for processing')
    parser.add_argument('--test', action='store_true',
                       help='Run in test mode (process only 3 PDFs)')
    parser.add_argument('--target-dpi', type=int, default=72,
                       help='Target DPI for PDF image compression (default: 72)')
    parser.add_argument('--max-workers', type=int, default=8,
                       help='Maximum number of parallel workers (default: 8)')
    parser.add_argument('--batch-size', type=int, default=10,
                       help='Number of PDFs to process in each batch (default: 10)')
    return parser.parse_args()

def get_pdf_hash(pdf_path: Path) -> str:
    """Generate a hash of the PDF file for caching purposes"""
    hasher = hashlib.sha256()
    with open(pdf_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b''):
            hasher.update(chunk)
    return hasher.hexdigest()

def get_cache_path(cache_dir: Path, pdf_hash: str) -> Path:
    """Get the path where the optimized PDF should be cached"""
    return cache_dir / f"{pdf_hash}.pdf"

def load_from_cache(cache_path: Path) -> Optional[bytes]:
    """Load optimized PDF from cache if it exists"""
    if cache_path.exists():
        try:
            with open(cache_path, 'rb') as f:
                return f.read()
        except Exception as e:
            print(f"Error reading from cache: {e}")
    return None

def save_to_cache(cache_path: Path, pdf_data: bytes) -> bool:
    """Save optimized PDF to cache"""
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, 'wb') as f:
            f.write(pdf_data)
        return True
    except Exception as e:
        print(f"Error saving to cache: {e}")
        return False

def optimize_pdf(pdf_data: bytes, target_dpi: int = 72) -> bytes:
    """
    Optimize PDF file size using Ghostscript with settings optimized for API submission.
    Uses specified DPI (screen resolution) and 60% JPEG quality since we don't need print quality
    for machine learning analysis.
    
    Args:
        pdf_data: Raw PDF bytes to optimize
        target_dpi: Target DPI for image compression
    
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
            f'-dColorImageResolution={target_dpi}',
            f'-dGrayImageResolution={target_dpi}',
            f'-dMonoImageResolution={target_dpi}',
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
            
            print(f"PDF optimized from {input_size:.1f}MB to {output_size:.1f}MB "
                  f"({(output_size/input_size)*100:.1f}% of original)")
            
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

def load_pdf(filepath: str, cache_dir: Path, target_dpi: int = 72) -> Optional[str]:
    """Load, optimize if needed, and base64 encode a PDF file"""
    try:
        pdf_path = Path(filepath)
        pdf_hash = get_pdf_hash(pdf_path)
        cache_path = get_cache_path(cache_dir, pdf_hash)
        
        # Try to load from cache first
        cached_data = load_from_cache(cache_path)
        if cached_data:
            print(f"Loading optimized PDF from cache: {cache_path}")
            return base64.b64encode(cached_data).decode('utf-8')
        
        # If not in cache, optimize and save
        print(f"Optimizing PDF: {filepath}")
        with open(filepath, 'rb') as f:
            pdf_data = f.read()
        
        # Optimize PDF
        optimized_data = optimize_pdf(pdf_data, target_dpi)
        
        # Save to cache if optimization was successful
        if optimized_data != pdf_data:
            if save_to_cache(cache_path, optimized_data):
                print(f"Saved optimized PDF to cache: {cache_path}")
        
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
- Time of observation (list with four allowed values: day/night/dusk/dawn)
- List of all flower visitors observed (use exact names as they appear in the publication)

Additionally, for each record, determine:
- Whether any of the flower visitors reported is a beetle (Coleoptera)
- Whether any of the effective pollinators reported is a beetle (Coleoptera)
- Whether the observations are unbiased. Consider as unbiased if made during multiple times during the day and night (no need to cover the full 24-hour cycle) and allowing for observations of flower visitors of multiple sizes and behaviors.
- Whether the main method was direct observation or direct sampling with a sweep net

Important considerations:
- Only include primary observations from the paper
- If a record involves more than one plant species or country, separate it into multiple records
- Do not add any variables to the output that are not explicitly listed in the example JSON structure
- if anything is unknown, use NAs.

Before providing your final output, please wrap your analysis in <paper_analysis> tags. In this analysis:

a. Identify and quote relevant sections of the paper that contain empirical primary observations of flower visitors. If there is no primary data, do not create any records and set has_visitor_data to false.
b. List out each plant species mentioned in these observations. Consider species as the smallest taxonomic unit for plants. If there are multiple varieties or subspecies, summarize all records for the same species as a single record.
c. For each plant species, extract the required information (location, method, time, visitors, etc.).
d. Assess whether any visitors or pollinators are beetles by listing out all visitors/pollinators and marking those that are beetles.
e. Evaluate whether the methods are unbiased by listing out the observation times and methods used.

This analysis ensures a thorough interpretation of the data. It's okay for this section to be quite long, as it may involve listing out multiple plant species and their associated information.

After your analysis, provide the final output in the following JSON format, wrapped in <output> tags. Here goes an explanation of the output data

<output_explanation>
{
  "has_primary_visitor_data": true/false (whether there are primary observations about flower visitors in this study),
  "has_visitor_notes": "brief explanation of evidence supporting the assessment in has_primary_visitor_data",
  "records": [ (ommit records if has_primary_visitor_data is false)
    {
      "country": "country name",
      "state_province": "state name",
      "locality": "location of the study",
      "plant_species": "plant species name",
      "method": "one-sentence description of methods of observation",
      "observation_time": list with four possible values: day,night,dawn,dusk,
      "visitors": list with all flower visitors observed,
      "beetle_visitors": whether beetles were found as flower visitors (boolean),
      "beetle_pollinators": whether beetles were found as significant pollinators (boolean),
      "methods_unbiased": whether methods appear to be unbiased (boolean),
      "methods_biased_reasoning": "one-sentence explanation for unbiased assessment",
      "methods_direct": whether main methods were direct sampling with a sweep net (boolean)
    }
  ]
}
</output_explanation>

And here goes an example:

<output>
{
  "has_visitor_data": true,
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
      "beetle_visitors": true,
      "beetle_pollinators": true,
      "methods_unbiased": true,
      "methods_biased_reasoning": "one-sentence explanation for unbiased assessment",
      "methods_direct": true
    }
  ]
}
</output>

Remember to be comprehensive in your analysis while avoiding false positives. Ensure that your output strictly adheres to the provided JSON structure without adding any additional variables.'''
    return prompt

def extract_json_from_response(text: str) -> Dict:
    """Extract JSON from XML output tags"""
    print(text)

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

def process_pdf_direct(client: Anthropic, pdf_data: str, doi: str, max_retries: int = 3) -> Dict:
    """Process a single PDF using direct API calls"""
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
                        },
                        {
                            "type": "text",
                            "text": create_extraction_prompt(doi)
                        }
                    ]
                }]
            )
            print(response.json())
            quit()
            result = extract_json_from_response(response.content[0].text)
            if result:
                # Override the DOI in the response with our BibTeX DOI
                result['doi'] = doi
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

def sanitize_custom_id(id_str: str) -> str:
    """Convert a DOI into a valid custom_id format for the batches API"""
    if not id_str:
        return "unknown_id"
    
    # Replace any invalid characters with underscores
    import re
    sanitized = re.sub(r'[^a-zA-Z0-9_-]', '_', id_str)
    
    # Truncate to 64 characters if needed (batch API limit)
    sanitized = sanitized[:64]
    
    # Ensure it's not empty
    if not sanitized:
        return "unknown_id"
        
    return sanitized

def process_pdfs_batch(client: Anthropic, pdf_list: List[tuple], batch_size: int = 10) -> Dict[str, Dict]:
    """Process multiple PDFs using Batches API in chunks"""
    all_results = {}
    
    # Process PDFs in batches
    for i in range(0, len(pdf_list), batch_size):
        batch = pdf_list[i:i + batch_size]
        print(f"\nProcessing batch {i//batch_size + 1} ({len(batch)} PDFs)")
        
        requests = []
        id_mapping = {}
        
        for doi, pdf_data in batch:
            sanitized_id = sanitize_custom_id(doi)
            id_mapping[sanitized_id] = doi
            
            requests.append(Request(
                custom_id=sanitized_id,
                params=MessageCreateParamsNonStreaming(
                    model="claude-3-5-sonnet-20241022",
                    max_tokens=4096,
                    temperature=0,
                    system="You are a scientific research assistant specializing in analyzing papers about plant-pollinator interactions.",
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

        try:
            message_batch = client.messages.batches.create(requests=requests)
            print(f"Created batch {message_batch.id} with {len(requests)} requests")
            
            while message_batch.processing_status == "in_progress":
                print("Waiting for batch processing...")
                time.sleep(30)
                message_batch = client.messages.batches.retrieve(message_batch.id)

            if message_batch.processing_status == "ended":
                for result in client.messages.batches.results(message_batch.id):
                    match result.result.type:
                        case "succeeded":
                            doi = id_mapping[result.custom_id]
                            extracted = extract_json_from_response(result.result.message.content[0].text)
                            if extracted:
                                all_results[doi] = extracted
                        case "errored":
                            print(f"Error processing {id_mapping[result.custom_id]}: {result.result.error}")
                        case "expired":
                            print(f"Request expired for {id_mapping[result.custom_id]}")
                        case "canceled":
                            print(f"Request canceled for {id_mapping[result.custom_id]}")
            else:
                print(f"Batch failed with status: {message_batch.processing_status}")
        except Exception as e:
            print(f"Error processing batch: {e}")
            continue
            
        # Add delay between batches
        time.sleep(5)
        
    return all_results

def optimize_pdf_worker(pdf_info: Tuple[str, Path, Path, int]) -> Tuple[str, Optional[str]]:
    """Worker function for parallel PDF optimization"""
    doi, pdf_path, cache_dir, target_dpi = pdf_info
    try:
        print(f"Optimizing PDF for DOI {doi}: {pdf_path}")
        pdf_data = load_pdf(str(pdf_path), cache_dir, target_dpi=target_dpi)
        return doi, pdf_data
    except Exception as e:
        print(f"Error optimizing PDF {pdf_path}: {e}")
        return doi, None

def main():
    args = parse_args()
    
    if not os.getenv('ANTHROPIC_API_KEY'):
        raise ValueError("Please set ANTHROPIC_API_KEY environment variable")
    
    client = Anthropic()
    
    # Create cache directory if it doesn't exist
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    print(f"Using PDF cache directory: {cache_dir}")
    
    pdf_collections_dir = Path(args.pdf_collections)
    doi_to_entry = load_bibtex_entries(pdf_collections_dir)
    print(f"Loaded {len(doi_to_entry)} entries from BibTeX files")
    
    results = {}
    if os.path.exists(args.output):
        with open(args.output, 'r') as f:
            results = json.load(f)
    
    # Prepare list of PDFs to optimize
    to_optimize = []
    for doi, entry in doi_to_entry.items():
        if doi and doi not in results:
            if 'resolved_pdf_path' in entry:
                pdf_path = Path(entry['resolved_pdf_path'])
                if pdf_path.exists():
                    print(f"Found PDF for DOI {doi}: {pdf_path}")
                    to_optimize.append((doi, pdf_path, cache_dir, args.target_dpi))
                else:
                    print(f"PDF file not found at resolved path: {pdf_path}")
            else:
                print(f"No PDF path found for DOI {doi}")
    
    if args.test:
        to_optimize = to_optimize[:10]
        print(f"Test mode: optimizing {len(to_optimize)} PDFs")
    
    # Optimize PDFs in parallel
    optimized_pdfs = {}
    if to_optimize:
        print(f"Optimizing {len(to_optimize)} PDFs using {args.max_workers} parallel workers...")
        with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            futures = [executor.submit(optimize_pdf_worker, pdf_info) for pdf_info in to_optimize]
            for future in as_completed(futures):
                doi, pdf_data = future.result()
                if pdf_data:
                    optimized_pdfs[doi] = pdf_data
    
    # Process PDFs sequentially with the API
    if optimized_pdfs:
        if args.use_batches:
            print(f"Processing {len(optimized_pdfs)} PDFs using Batches API...")
            to_process = [(doi, pdf_data) for doi, pdf_data in optimized_pdfs.items()]
            batch_results = process_pdfs_batch(client, to_process)
            results.update(batch_results)
        else:
            for doi, pdf_data in optimized_pdfs.items():
                print(f"\nProcessing PDF for DOI: {doi}")
                result = process_pdf_direct(client, pdf_data, doi)
                if result:
                    results[doi] = result
                    # Save results after each PDF is processed
                    with open(args.output, 'w') as f:
                        json.dump(results, f, indent=2)
                time.sleep(1)  # Rate limiting
    
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
