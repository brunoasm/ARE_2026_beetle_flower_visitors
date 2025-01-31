import anthropic
from anthropic import Anthropic
import os
import re
import json
import argparse
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import time
from pybtex.database.input import bibtex
import base64
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

# Configuration variables for easy modification
BATCH_SIZE = 6                # Number of PDFs to process in each batch
SIMULTANEOUS_BATCHES = 4       # Number of batches to process simultaneously
TEST_MODE_BATCHES = 20          # Number of batches to process in test mode
BATCH_CHECK_INTERVAL = 30      # Seconds between batch status checks
BATCH_SUBMISSION_INTERVAL = 20 # Seconds between batch submissions
BIBTEX_PATH = 'pdfs/export_20250131/export_20250131.updated.bib'

def parse_args():
    """Parse command line arguments with sensible defaults for project structure"""
    project_root = Path.cwd()
    
    parser = argparse.ArgumentParser(description='Extract flower visitor information from PDFs')
    parser.add_argument('--output', 
                       default=str(project_root / 'analysis' / 'flower_visitor_records.json'),
                       help='Output JSON file')
    parser.add_argument('--test', action='store_true',
                       help=f'Run in test mode (process {TEST_MODE_BATCHES} batches)')
    return parser.parse_args()

def load_bibtex_entries(bib_path: Path) -> Dict[str, Dict]:
    """
    Load BibTeX entries and create a mapping of IDs to their entries using pybtex.
    """
    print(f"Processing BibTeX file: {bib_path}")
    
    parser = bibtex.Parser()
    id_to_entry = {}
    
    try:
        bib_data = parser.parse_file(bib_path)
        
        for key, entry in bib_data.entries.items():
            entry_dict = {
                'type': entry.type,
                'key': key,
                **{k: v for k, v in entry.fields.items()}
            }
            
            # Process the file field if present
            if 'file' in entry.fields:
                try:
                    file_field = entry.fields['file']
                    if file_field.startswith('{') and file_field.endswith('}'):
                        file_field = file_field[1:-1]
                    
                    for file_entry in file_field.split(';'):
                        parts = file_entry.strip().split(':')
                        if len(parts) == 3 and parts[2].lower() == 'application/pdf':
                            entry_dict['pdf_path'] = parts[1].rstrip("\n")
                            break

                    print(f"Raw file field: '{file_field}'")
                    print(f"Extracted file path: '{entry_dict.get('pdf_path', 'N/A')}'")
                except Exception as e:
                    print(f"Warning: Could not process file field for ID {key}: {str(e)}")
            
            id_to_entry[key] = entry_dict
    
        print(f"Successfully loaded {len(id_to_entry)} entries")
        
    except Exception as e:
        print(f"Error processing BibTeX file: {str(e)}")
        print("File path attempted:", bib_path)
        raise
    
    return id_to_entry

def create_extraction_prompt():
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

Important considerations:
- Only include primary observations from the paper. Do not consider secondary data or information.
- If a record involves more than one plant species or country, separate it into multiple records. Each record must have a single country and a single plant species.
- Do not add any variables to the output that are not explicitly listed in the example JSON structure.
- Do not use external information to update taxonomic names. List common names and taxonomic names as they are written in the source.
- if anything is unknown, use `none` or empty lists, following JSON best practices.

<paper_analysis>
1. Identify and quote relevant sections of the paper that contain empirical primary observations of flower visitors. If there is no primary data, explain why and do not create any records.

2. List out each plant species mentioned in these observations. Consider species as the smallest taxonomic unit for plants. If there are multiple varieties or subspecies, summarize all records for the same species as a single record. Number each species as you list them.

3. For each plant species, extract the required information:
   - Quote the relevant section(s) for this species
   - Location (country, state/province, locality)
   - Method of observation
   - Time of observation (list and count each time period mentioned)
   - List of flower visitors. Number each visitor as you list them. Be comprehensive and list every single visitor mentioned in the source. Do not summarize

4. Assess whether any visitors or pollinators are beetles:
   - For each visitor/pollinator listed, classify it as either "Beetle (Coleoptera)" or "Non-beetle". List family if it is a beetle.

5. Evaluate whether the methods are unbiased:
   - List out all observation times mentioned
   - Describe the methods used
   - Determine if they allow for observations of multiple sizes and behaviors

6. Double-check your findings for accuracy and completeness.Ensure that you haven't missed any relevant information or made any incorrect assumptions.

7. Summarize any noteworthy facts about beetles discovered in this study, if any.
</paper_analysis>

This analysis ensures a thorough interpretation of the data. It is okay for this section to be quite long, as it may involve listing out multiple plant species and their associated information. Always be thorough during the analysis and list all of the data necessary to retrieve all records and all flower visitors for each record.

After your analysis, provide the final output in the following JSON format, wrapped in <output> tags. Here goes an explanation of the output data

<output_explanation>
{
  "has_primary_visitor_data": whether there are primary observations about flower visitors in this study (boolean),
  "has_visitor_notes": brief explanation of evidence supporting the assessment in has_primary_visitor_data (string),
  "noteworthy_beetle_fact": one or two sentences summarizing noteworthy facts about beetles discovered in this study (string),
  "records": [ (empty list if `has_primary_visitor_data` is False)
    {
      "country": country name (string),
      "state_province": state name (string),
      "locality": location of the observation record (string),
      "plant_species": plant species name (string),
      "method": one-sentence description of methods of observation (string),
      "observation_time": list with four possible values: day,night,dawn,dusk (list of strings),
      "visitors": list with all flower visitors observed (list of strings),
      "beetle_families": list all beetle families mentioned as flower visitors in the text (list of strings)
      "beetle_visitors": whether beetles were found as flower visitors (boolean),
      "beetle_pollinators": whether beetles were found as significant pollinators (boolean),
      "methods_unbiased": whether methods appear to be unbiased (boolean),
      "methods_biased_reasoning": one-sentence explanation for unbiased assessment (string)
    }
  ]
}
</output_explanation>

And here goes an output example:

<output>
{
  "has_visitor_data": true,
  "has_visitor_notes": "brief explanation of evidence supporting the true assessment",
  "noteworthy_beetle_fact": "Some fact"
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
      "beetle_families": ["family1"],
      "beetle_pollinators": true,
      "methods_unbiased": true,
      "methods_biased_reasoning": "one-sentence explanation for unbiased assessment"
    }
  ]
}
</output>


Remember to be comprehensive in your analysis while avoiding false positives. Ensure that your output strictly adheres to the provided JSON structure without adding any additional variables. It needs to be a parseable json. Always include all records in the response, even if it ends up being extremely long. Never ask me to continue or whether the response should be complete, just include all of the records in the output.'''

    return prompt

def extract_json_from_response(text: str) -> Dict:
    """Extract JSON from XML output tags in Claude's response"""
    match = re.search(r'<output>\s*(\{.*?\})\s*</output>', text, re.DOTALL)
    if match:
        json_str = match.group(1)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            print(f"Failed to parse JSON: {json_str}")
            return None
    return None
    
def extract_analysis_from_response(text: str) -> str:
    """Extract analysis from XML paper_analysis tags in Claude's response"""
    match = re.search(r'<paper_analysis>(.*?)</paper_analysis>', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None

def wait_for_batches(client: Anthropic, batch_ids: List[str]) -> Dict[str, str]:
    """Wait for multiple batches to complete and return their statuses"""
    print(f"\nWaiting for {len(batch_ids)} batches to complete...")
    
    incomplete = {id: "in_progress" for id in batch_ids}
    start_time = time.time()
    
    while incomplete:
        time.sleep(BATCH_CHECK_INTERVAL)
        
        for batch_id in list(incomplete.keys()):
            batch = client.messages.batches.retrieve(batch_id)
            if batch.processing_status != "in_progress":
                incomplete.pop(batch_id)
                print(f"Batch {batch_id} completed with status: {batch.processing_status}")
        
        elapsed = int(time.time() - start_time)
        if incomplete:
            print(f"Still waiting on {len(incomplete)} batches after {elapsed} seconds...")
    
    return {id: client.messages.batches.retrieve(id).processing_status for id in batch_ids}

def save_results(results: Dict, filename: str = None):
    """Save current results to JSON file with proper formatting"""
    if filename:
        output_path = Path(filename)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(results, f, indent=2)

def process_batches_window(client: Anthropic, all_pdfs: List[tuple], output_file: str) -> Dict[str, Dict]:
    """Process PDFs in sliding windows of multiple simultaneous batches"""
    results = {}
    
    for window_start in range(0, len(all_pdfs), SIMULTANEOUS_BATCHES * BATCH_SIZE):
        window_pdfs = all_pdfs[window_start:window_start + (SIMULTANEOUS_BATCHES * BATCH_SIZE)]
        print(f"\nProcessing window starting at index {window_start} ({len(window_pdfs)} PDFs)")
        
        # Create multiple batches for this window
        active_batches = {}  # batch_id -> id_mapping
        
        for batch_start in range(0, len(window_pdfs), BATCH_SIZE):
            batch_pdfs = window_pdfs[batch_start:batch_start + BATCH_SIZE]
            requests = []
            id_mapping = {}
            
            for bib_id, pdf_data in batch_pdfs:
                id_mapping[bib_id] = bib_id  # No sanitization needed for BibTeX IDs
                
                requests.append(Request(
                    custom_id=bib_id,
                    params=MessageCreateParamsNonStreaming(
                        model="claude-3-5-sonnet-20241022",
                        max_tokens=8192,
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
                                    "text": create_extraction_prompt()
                                }
                            ]
                        }]
                    )
                ))
            
            try:
                message_batch = client.messages.batches.create(requests=requests)
                print(f"Created batch {message_batch.id} with {len(requests)} requests")
                active_batches[message_batch.id] = id_mapping
                time.sleep(BATCH_SUBMISSION_INTERVAL)
            except Exception as e:
                print(f"Error creating batch: {e}")
                continue
        
        # Wait for all batches in this window to complete
        batch_statuses = wait_for_batches(client, list(active_batches.keys()))
        
        # Process results from all completed batches
        window_results = {}
        for batch_id, status in batch_statuses.items():
            if status == "ended":
                id_mapping = active_batches[batch_id]
                for result in client.messages.batches.results(batch_id):
                    bib_id = id_mapping[result.custom_id]
                    match result.result.type:
                        case "succeeded":
                            extracted = {
                                'status': 'success',
                                'json': extract_json_from_response(result.result.message.content[0].text),
                                'analysis': extract_analysis_from_response(result.result.message.content[0].text)
                            }
                            window_results[bib_id] = extracted
                        case "errored":
                            print(f"Error processing {id_mapping[result.custom_id]}: {result.result.error}")
                            window_results[bib_id] = {
                                'status': 'error',
                                'error_message': str(result.result.error)
                            }
                        case _:
                            window_results[bib_id] = {
                                'status': result.result.type,
                                'error_message': f"Unexpected result type: {result.result.type}"
                            }
        
        # Update and save results after each window
        results.update(window_results)
        save_results(results, output_file)
        time.sleep(5)  # Brief delay between windows
    
    return results

def main():
    """Main execution function that coordinates the entire process"""
    args = parse_args()
    
    if not os.getenv('ANTHROPIC_API_KEY'):
        raise ValueError("Please set ANTHROPIC_API_KEY environment variable")
    
    client = Anthropic()
    
    # Load BibTeX entries
    bib_path = Path(BIBTEX_PATH)
    id_to_entry = load_bibtex_entries(bib_path)
    print(f"Loaded {len(id_to_entry)} entries from BibTeX file")
    
    # Load existing results if any
    results = {}
    if os.path.exists(args.output):
        with open(args.output, 'r') as f:
            results = json.load(f)
    
    # Prepare list of PDFs to process
    to_process = []
    for bib_id, entry in id_to_entry.items():
        if bib_id not in results:
            if 'pdf_path' in entry:
                # Construct full path by joining BibTeX directory with relative path
                bibtex_dir = Path(BIBTEX_PATH).parent
                pdf_path = bibtex_dir / entry['pdf_path']
                
                if pdf_path.exists():
                    try:
                        with open(pdf_path, 'rb') as f:
                            pdf_data = base64.b64encode(f.read()).decode('utf-8')
                            to_process.append((bib_id, pdf_data))
                    except Exception as e:
                        print(f"Error reading PDF for ID {bib_id}: {e}")
                else:
                    print(f"PDF file not found: {pdf_path}")
    
    if args.test:
        # In test mode, process specified number of batches
        max_pdfs = TEST_MODE_BATCHES * BATCH_SIZE
        to_process = to_process[:max_pdfs]
        print(f"Test mode: processing {len(to_process)} PDFs in {TEST_MODE_BATCHES} batches")
    
    # Process PDFs in batch windows
    if to_process:
        batch_results = process_batches_window(client, to_process, args.output)
        results.update(batch_results)
    
    # Save final results
    save_results(results, args.output)
    
    # Print summary statistics
    total_processed = len(results)
    successful_processes = sum(1 for r in results.values() if r.get('status') == 'success')
    failed_processes = sum(1 for r in results.values() if r.get('status') == 'error')
    
    total_records = sum(
        len(r.get('json', {}).get('records', [])) 
        for r in results.values() 
        if r.get('status') == 'success'
    )
    
    records_with_beetles = sum(
        1 for r in results.values() 
        if r.get('status') == 'success'
        for record in r.get('json', {}).get('records', []) 
        if record.get('beetle_visitors') or record.get('beetle_pollinators')
    )
    
    print(f"\nProcessing complete!")
    print(f"Total PDFs processed: {total_processed}")
    print(f"Successful processes: {successful_processes}")
    print(f"Failed processes: {failed_processes}")
    print(f"Total flower visitor records extracted: {total_records}")
    print(f"Records involving beetles: {records_with_beetles}")
    print(f"Results saved to {args.output}")

if __name__ == "__main__":
    main()
