# Import necessary libraries
import anthropic
from anthropic import Anthropic
import os, re
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

def optimize_pdf(pdf_data: bytes, target_dpi: int = 72) -> bytes:
    """Optimize PDF for API transmission using aggressive compression techniques."""
    input_buffer = io.BytesIO(pdf_data)
    output_buffer = io.BytesIO()
    
    try:
        with pikepdf.Pdf.open(input_buffer) as pdf:
            try:
                # Remove unnecessary elements
                for page in pdf.pages:
                    try:
                        # Clean up page elements
                        for key in ['/Annots', '/AF', '/AA', '/Thumb', '/PieceInfo', '/Metadata']:
                            if key in page:
                                del page[key]
                        
                        # Process images with enhanced compression
                        resources = page.get('/Resources', {})
                        if '/XObject' in resources:
                            xobjects = resources['/XObject']
                            for key, xobject in list(xobjects.items()):
                                try:
                                    if (isinstance(xobject, pikepdf.Stream) and 
                                        xobject.get('/Subtype') == '/Image'):
                                        try:
                                            width = float(xobject.get('/Width', 0))
                                            height = float(xobject.get('/Height', 0))
                                            if width > 0 and height > 0:
                                                bbox = [float(x) for x in page.get('/MediaBox')]
                                                page_width = bbox[2] - bbox[0]
                                                if page_width > 0:
                                                    # Calculate current DPI
                                                    orig_dpi = int(72 * width / page_width)
                                                    
                                                    # Apply more aggressive scaling for larger images
                                                    if orig_dpi > target_dpi:
                                                        scale = min(target_dpi / orig_dpi, 0.5)  # Maximum 50% reduction
                                                        new_width = max(int(width * scale), 300)  # Minimum width 300px
                                                        new_height = max(int(height * scale), 300)  # Minimum height 300px
                                                        
                                                        # Process different color spaces
                                                        color_space = xobject.get('/ColorSpace')
                                                        if color_space in ['/DeviceRGB', '/DeviceGray']:
                                                            try:
                                                                # Apply JPEG compression
                                                                stream_dict = {
                                                                    'Type': pikepdf.Name('/XObject'),
                                                                    'Subtype': pikepdf.Name('/Image'),
                                                                    'Width': new_width,
                                                                    'Height': new_height,
                                                                    'BitsPerComponent': 8,
                                                                    'ColorSpace': pikepdf.Name(color_space),
                                                                    'Filter': pikepdf.Name('/DCTDecode'),
                                                                    'Quality': 60  # More aggressive JPEG compression
                                                                }
                                                                xobject.write(
                                                                    pikepdf.Stream(pdf, 
                                                                                 xobject.read_raw_bytes(),
                                                                                 stream_dict)
                                                                )
                                                            except Exception as e:
                                                                print(f"Warning: Failed to optimize image: {str(e)}")
                                        except Exception as e:
                                            print(f"Warning: Error processing image dimensions: {str(e)}")
                                except Exception as e:
                                    print(f"Warning: Error processing XObject: {str(e)}")
                    except Exception as e:
                        print(f"Warning: Error processing page: {str(e)}")
                        continue
                
                # Remove document-level metadata
                try:
                    with pdf.open_metadata() as meta:
                        meta.clear()
                except Exception as e:
                    print(f"Warning: Failed to clear metadata: {str(e)}")
                
                # Remove additional document-level elements
                for key in ['/AcroForm', '/Metadata', '/OCProperties', '/StructTreeRoot']:
                    if key in pdf.Root:
                        try:
                            del pdf.Root[key]
                        except Exception as e:
                            print(f"Warning: Failed to remove {key}: {str(e)}")
                
                # Save with maximum compression
                pdf.save(output_buffer,
                        compress_streams=True,
                        preserve_pdfa=False,
                        object_stream_mode=pikepdf.ObjectStreamMode.generate,
                        linearize=False)  # Disable linearization for smaller file size
                
            except Exception as e:
                print(f"Warning: Error during PDF optimization: {str(e)}")
                return pdf_data
    
    except Exception as e:
        print(f"Warning: Failed to open PDF: {str(e)}")
        return pdf_data
    
    optimized_data = output_buffer.getvalue()
    
    # Print size comparison
    input_size = len(pdf_data) / 1024 / 1024  # Convert to MB
    output_size = len(optimized_data) / 1024 / 1024  # Convert to MB
    print(f"PDF optimization: {input_size:.2f}MB -> {output_size:.2f}MB "
          f"({(output_size/input_size)*100:.1f}% of original)")
    
    # Check final size
    if len(optimized_data) > 32 * 1024 * 1024:
        raise ValueError(f"Optimized PDF size ({output_size:.1f}MB) exceeds 32MB limit")
    
    return optimized_data

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
        optimized_data = optimize_pdf(pdf_data, target_dpi)
        
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
    prompt = '''Your objective is to carefully analyze this paper and extract empirical observations of flower visitors. Follow these steps:

1. Determine if the paper contains any empirical observations of flower visitors.
2. If yes, extract all records of flower visitors. Each record should represent observations of one plant species in one country.

For each record, you must extract the following information:
- Country where observations were made
- Plant species (use the most precise taxonomic level provided in the paper)
- Method of observation (brief description)
- Time of observation (day/night/both)
- List of all flower visitors observed (use exact names as they appear in the publication). Be comprehensive but avoid false positives

Additionally, for each record, you need to determine:
- beetle_visitors: Whether any of the flower visitors reported is a beetle (Coleoptera)
- beetle_pollinators: Whether any of the effective pollinators reported is a beetle (Coleoptera)
- methods_unbiased: Whether the observations are unbiased. Unbiased observations must have been made during multiple times during the day and the night, and allowed for observations of flower visitors of multiple sizes and behaviors. For example, colored traps bias towards insects attracted to those colors, sweep nets are biased towards insects that fly more and that are intermediate in size. If the study specifically assumes the identity of the pollinators and flower visitors before performing observations, the methods are biased.
- methods_biased_resoning: one-sentence explanation for the evidence supporting the assessment of 'nmethods_unbiased"
- methods_net: Whether the main method was direct sampling with a sweep net

Important considerations:
- Only include primary observations from the paper
- If a record involves more than one plant species or country, separate it into multiple records

Provide your thought process in <extraction_process> tags, then provide the final output in this JSON format. Wrap your JSON response in <output> tags. For example:
<output>
{
  "doi": "paper_doi",
  "has_visitor_data": true/false,
  "records": [
    {
      "country": "country_name",
      "plant_species": "species_name",
      "method": "brief_method_description",
      "observation_time": "day/night/both",
      "visitors": ["visitor1", "visitor2", ...],
      "beetle_visitors": true/false,
      "beetle_pollinators": true/false,
      "methods_unbiased": true/false,
      "methods_net": true/false
    }
  ]
}
</output>'''
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
