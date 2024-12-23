import pandas as pd
import gzip
from anthropic import Anthropic
import os
import time
from pathlib import Path
import json
import argparse
from typing import List, Dict
import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.customization import convert_to_unicode
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='Classify flower visitor studies from BibTeX files')
    parser.add_argument('--use-batches', action='store_true', 
                       help='Use the Batches API for processing')
    parser.add_argument('--test', action='store_true',
                       help='Run in test mode (process only 10 records)')
    return parser.parse_args()

def load_bibtex(filename: str) -> List[Dict]:
    """Load and parse a BibTeX file"""
    parser = BibTexParser()
    parser.customization = convert_to_unicode
    
    with open(filename, 'r', encoding='utf-8') as bibtex_file:
        bib_database = bibtexparser.load(bibtex_file, parser=parser)
    return bib_database.entries

def load_results(filename='analysis/flower_visitor_classifications.json'):
    """Load existing classification results"""
    if Path(filename).exists():
        with open(filename, 'r') as f:
            return json.load(f)
    return {}

def save_results(results, filename='analysis/flower_visitor_classifications.json'):
    """Save classification results to JSON file"""
    # Ensure the analysis directory exists
    Path(filename).parent.mkdir(exist_ok=True)
    with open(filename, 'w') as f:
        json.dump(results, f, indent=2)

def create_classification_prompt(entry: Dict) -> str:
    """Create the prompt for classification"""
    title = entry.get('title', '')
    abstract = entry.get('abstract', '')
    
    return f"""Here is the title and abstract of the paper you need to analyze:

<title>
{title}
</title>

<abstract>
{abstract}
</abstract>

Your goal is to determine two key aspects of the paper:

1. Does the paper report empirical observations of animals visiting angiosperm flowers or gymnosperm reproductive structures? (has_visitor_data)
2. Does the paper make inferences about pollination agents based solely on plant morphology/physiology, without direct observation? (infers_from_plant)

Important considerations:
- Be cautious not to confuse seed predators or other non-pollinating visitors with actual pollinators.
- Consider both angiosperms (flowering plants) and gymnosperms (non-flowering plants with analogous reproductive structures).
- Distinguish between morphological descriptions related to pollination agent estimation and those unrelated to pollination.

Based on your analysis, provide your determination in the form of a JSON object with two boolean values:
1. "has_visitor_data": true if the study likely contains primary observations or experiments about pollinators and other flower visitors, false otherwise.
2. "infers_from_plant": true if the study likely infers pollinating agents based on plant morphology/physiology alone, without direct observation, false otherwise.

Wrap your JSON response in <output> tags. For example:
<output>
{{"has_visitor_data": false, "infers_from_plant": true}}
</output>

Ensure that your determination is based solely on the information provided in the title and abstract, making your best inference about the full study's content."""

def extract_json_from_xml(text: str) -> Dict:
    """Extract JSON from XML output tags"""
    import re
    
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

def classify_study_direct(client: Anthropic, entry: Dict) -> Dict:
    """Use Claude API directly to classify a study"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=4096,
                temperature=0,
                system="You are a meticulous pollination biologist with extensive experience in reviewing scientific literature on plant-pollinator interactions. Your task is to analyze a scientific paper's title and abstract to predict its content regarding pollination and flower visitation to produce structured data for a meta-analysis.",
                messages=[{
                    "role": "user",
                    "content": create_classification_prompt(entry)
                }]
            )
            result = extract_json_from_xml(response.content[0].text.strip())
            if result:
                return result
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"Failed to classify after {max_retries} attempts: {e}")
                return None
            time.sleep(2 ** attempt)

def sanitize_custom_id(id_str: str) -> str:
    """Convert an ID into a valid custom_id format for the batches API"""
    if not id_str:
        return "unknown_id"
    
    # Replace any invalid characters with underscores
    import re
    sanitized = re.sub(r'[^a-zA-Z0-9_-]', '_', id_str)
    
    # Truncate to 64 characters if needed
    sanitized = sanitized[:64]
    
    # Ensure it's not empty
    if not sanitized:
        return "unknown_id"
        
    return sanitized

def classify_studies_batch(client: Anthropic, entries: List[Dict]) -> Dict[str, Dict]:
    """Use Claude Batches API to classify multiple studies"""
    # Prepare batch requests
    requests = []
    id_mapping = {}  # To map sanitized IDs back to original IDs
    
    for entry in entries:
        original_id = entry.get('doi', entry.get('ID', ''))
        sanitized_id = sanitize_custom_id(original_id)
        id_mapping[sanitized_id] = original_id
        
        requests.append(Request(
            custom_id=sanitized_id,
            params=MessageCreateParamsNonStreaming(
                model="claude-3-5-sonnet-20241022",
                max_tokens=4096,
                temperature=0,
                messages=[{
                    "role": "user",
                    "content": create_classification_prompt(entry)
                }],
                system="You are a meticulous pollination biologist with extensive experience in reviewing scientific literature on plant-pollinator interactions. Your task is to analyze a scientific paper's title and abstract to predict its content regarding pollination and flower visitation to produce structured data for a meta-analysis."
            )
        ))

    # Create batch
    message_batch = client.messages.batches.create(requests=requests)
    
    # Poll for completion
    while message_batch.processing_status == "in_progress":
        print("Waiting for batch processing...")
        time.sleep(30)
        message_batch = client.messages.batches.retrieve(message_batch.id)

    # Process results
    if message_batch.processing_status == "ended":
        batch_results = process_batch_results(client, message_batch.id)
        # Map results back to original IDs
        return {id_mapping[sanitized_id]: result 
                for sanitized_id, result in batch_results.items()}
    else:
        print(f"Batch failed with status: {message_batch.processing_status}")
        return {}

def process_batch_results(client: Anthropic, batch_id: str) -> Dict[str, Dict]:
    """Process results from a batch classification"""
    results = {}
    for result in client.messages.batches.results(batch_id):
        match result.result.type:
            case "succeeded":
                try:
                    classification = extract_json_from_xml(result.result.message.content[0].text.strip())
                    if classification:
                        results[result.custom_id] = classification
                    else:
                        print(f"Failed to extract JSON for {result.custom_id}")
                        results[result.custom_id] = None
                except Exception as e:
                    print(f"Error processing result for {result.custom_id}: {e}")
                    results[result.custom_id] = None
            case "errored":
                print(f"Request error for {result.custom_id}: {result.result.error}")
                results[result.custom_id] = None
            case "expired":
                print(f"Request expired for {result.custom_id}")
                results[result.custom_id] = None
            case "canceled":
                print(f"Request canceled for {result.custom_id}")
                results[result.custom_id] = None
    return results

def save_classifications_csv(entries: List[Dict], results: Dict, filename='analysis/classified_studies.csv'):
    """Save classifications to CSV file"""
    records = []
    for entry in entries:
        id_key = entry.get('doi', entry.get('ID', ''))
        classification = results.get(id_key, {})
        
        record = {
            'id': id_key,
            'title': entry.get('title', ''),
            'year': entry.get('year', ''),
            'doi': entry.get('doi', ''),
            'journal': entry.get('journal', ''),
            'has_visitor_data': classification.get('has_visitor_data', None),
            'infers_from_plant': classification.get('infers_from_plant', None)
        }
        records.append(record)
    
    df = pd.DataFrame(records)
    df.to_csv(filename, index=False)

def main():
    args = parse_args()
    
    # Check for API key
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        raise ValueError("Please set ANTHROPIC_API_KEY environment variable")
    
    # Initialize Anthropic client
    client = Anthropic(api_key=api_key)
    
    # Load BibTeX files
    entries = []
    print("Load bibtex")
    if args.test:
        entries.extend(load_bibtex('analysis/test.bib'))
    else:
        entries.extend(load_bibtex('analysis/unfiltered_doi.bib'))
        #entries.extend(load_bibtex('analysis/unfiltered_nodoi.bib'))
    print("Bibtex loaded")
    
    # Apply test mode limit if specified
    if args.test:
        entries = entries[:10]
        print("Running in test mode with 10 records")
    
    # Load existing results
    results = load_results()
    
    # Process entries
    to_process = []
    for entry in entries:
        id_key = entry.get('doi', entry.get('ID', ''))
        if id_key and id_key not in results:
            if entry.get('title') or entry.get('abstract'):
                to_process.append(entry)

    if to_process:
        if args.use_batches:
            print(f"Processing {len(to_process)} studies using Batches API...")
            batch_results = classify_studies_batch(client, to_process)
            results.update(batch_results)
            save_results(results)
        else:
            for entry in to_process:
                id_key = entry.get('doi', entry.get('ID', ''))
                print(f"\nProcessing entry: {id_key}")
                classification = classify_study_direct(client, entry)
                results[id_key] = classification
                save_results(results)
                time.sleep(1)
    
    # Save final results to CSV
    save_classifications_csv(entries, results)
    
    # Print summary
    total = len(results)
    visitor_studies = sum(1 for x in results.values() if x and x.get('has_visitor_data'))
    inference_studies = sum(1 for x in results.values() if x and x.get('has_morphological_inference'))
    print(f"\nProcessing complete!")
    print(f"Total papers processed: {total}")
    print(f"Papers with flower visitor data: {visitor_studies}")
    print(f"Papers with morphological inferences: {inference_studies}")
    print(f"Results saved to analysis/flower_visitor_classifications.json")
    print(f"CSV summary saved to analysis/classified_studies.csv")

if __name__ == "__main__":
    main()
