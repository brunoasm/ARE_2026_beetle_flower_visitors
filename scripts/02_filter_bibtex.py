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
    # Extract title and abstract from BibTeX entry
    title = entry.get('title', '')
    abstract = entry.get('abstract', '')
    
    return f"""Based on the following title and abstract, respond only with a JSON object containing two boolean values:

1. "has_visitor_data": Are empirical observations of animals visiting flowers reported?
2. "has_morphological_inference": Are inferences made about potential pollinators based solely on plant morphology/physiology (without direct observation)?

Consider these criteria:
For has_visitor_data:
- The study should contain actual observations or measurements of any animals visiting flowers
- This includes all flower visitors, regardless of whether they are confirmed pollinators
- The data should be empirical (collected through observation or experiment)
- Include studies that document any animal-flower interactions

For has_morphological_inference:
- Look for inferences about pollination syndromes based on flower morphology
- Include cases where pollinator types are proposed based on flower structure
- Include studies that discuss adaptations to specific pollinators based on floral traits
- The inference should be about potential animal pollinators (not wind or self-pollination)

Title: {title}
Abstract: {abstract}

Respond with a JSON object containing only these two boolean values. Example:
{{"has_visitor_data": false, "has_morphological_inference": true}}"""

def classify_study_direct(client: Anthropic, entry: Dict) -> Dict:
    """Use Claude API directly to classify a study"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model="claude-3-sonnet-20240229",
                max_tokens=100,
                temperature=0,
                messages=[{
                    "role": "user",
                    "content": create_classification_prompt(entry)
                }]
            )
            return json.loads(response.content[0].text.strip())
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"Failed to classify after {max_retries} attempts: {e}")
                return None
            time.sleep(2 ** attempt)

def process_batch_results(client: Anthropic, batch_id: str) -> Dict[str, Dict]:
    """Process results from a batch classification"""
    results = {}
    for result in client.messages.batches.results(batch_id):
        if result.result.type == "succeeded":
            try:
                classification = json.loads(result.result.message.content[0].text.strip())
                results[result.custom_id] = classification
            except Exception as e:
                print(f"Error processing result for {result.custom_id}: {e}")
                results[result.custom_id] = None
        else:
            print(f"Failed result for {result.custom_id}: {result.result.type}")
            results[result.custom_id] = None
    return results

def classify_studies_batch(client: Anthropic, entries: List[Dict]) -> Dict[str, Dict]:
    """Use Claude Batches API to classify multiple studies"""
    # Prepare batch requests
    requests = []
    for entry in entries:
        id_key = entry.get('doi', entry.get('ID', ''))
        requests.append({
            "custom_id": id_key,
            "params": {
                "model": "claude-3-sonnet-20240229",
                "max_tokens": 100,
                "temperature": 0,
                "messages": [{
                    "role": "user",
                    "content": create_classification_prompt(entry)
                }]
            }
        })

    # Create batch
    batch = client.messages.batches.create(requests=requests)
    
    # Poll for completion
    while batch.processing_status == "in_progress":
        print("Waiting for batch processing...")
        time.sleep(30)
        batch = client.messages.batches.retrieve(batch.id)

    # Process results
    if batch.processing_status == "ended":
        return process_batch_results(client, batch.id)
    else:
        print(f"Batch failed with status: {batch.processing_status}")
        return {}

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
            'has_morphological_inference': classification.get('has_morphological_inference', None)
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
    entries.extend(load_bibtex('analysis/unfiltered_doi.bib'))
    entries.extend(load_bibtex('analysis/unfiltered_nodoi.bib'))
    
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
