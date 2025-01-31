#!/bin/bash

# Exit on error
set -e

# Function to convert bytes to human readable format
format_size() {
    local size=$1
    
    if [ $size -lt 1024 ]; then
        echo "${size}B"
        return
    fi
    
    size=$(echo "scale=2; $size/1024" | bc)
    if [ $(echo "$size < 1024" | bc) -eq 1 ]; then
        echo "${size}KB"
        return
    fi
    
    size=$(echo "scale=2; $size/1024" | bc)
    if [ $(echo "$size < 1024" | bc) -eq 1 ]; then
        echo "${size}MB"
        return
    fi
    
    size=$(echo "scale=2; $size/1024" | bc)
    echo "${size}GB"
}

# Function to compress a single PDF file
compress_pdf() {
    local input_file="$1"
    local temp_dir
    local basename
    local temp_ps
    local temp_pdf
    local backup_file
    
    # Create a temporary directory for this file
    temp_dir=$(mktemp -d)
    basename=$(basename "$input_file" .pdf)
    temp_ps="${temp_dir}/${basename}.ps"
    temp_pdf="${temp_dir}/${basename}_compressed.pdf"
    backup_file="${input_file}.backup"

    {
        echo "Processing: $input_file"
        
        # Get original size in bytes
        original_size=$(stat -f %z "$input_file")
        echo "Original size: $(format_size $original_size)"

        # Create backup
        cp "$input_file" "$backup_file"

        # Attempt compression
        if pdftops "$input_file" "$temp_ps" 2>/dev/null && \
           ps2pdf -dPDFSETTINGS=/ebook "$temp_ps" "$temp_pdf" 2>/dev/null; then
            
            if [ -f "$temp_pdf" ]; then
                compressed_size=$(stat -f %z "$temp_pdf")
                
                if [ $(echo "$compressed_size < $original_size" | bc) -eq 1 ]; then
                    mv "$temp_pdf" "$input_file"
                    rm "$backup_file"
                    
                    echo "Compression successful!"
                    echo "New size: $(format_size $compressed_size)"
                    
                    saved_space=$(echo "$original_size - $compressed_size" | bc)
                    saved_percent=$(echo "scale=2; ($saved_space * 100) / $original_size" | bc)
                    
                    echo "Reduced by: $(format_size $saved_space) ($saved_percent%)"
                else
                    echo "Compressed file is larger than original, keeping original"
                    mv "$backup_file" "$input_file"
                fi
            else
                echo "Error: Compression failed, restoring from backup"
                mv "$backup_file" "$input_file"
            fi
        else
            echo "Error: Compression failed, restoring from backup"
            mv "$backup_file" "$input_file"
        fi

        # Clean up temporary files
        rm -rf "$temp_dir"
        [ -f "$backup_file" ] && rm "$backup_file"
        echo "----------------------------------------"
    } # End of grouped output
}

export -f format_size compress_pdf

# Main script
SOURCE_DIR="pdfs/export_20250131/files"

# Check if source directory exists
if [ ! -d "$SOURCE_DIR" ]; then
    echo "Error: Source directory $SOURCE_DIR does not exist."
    exit 1
fi

# Check if required commands exist
for cmd in pdftops ps2pdf bc parallel; do
    if ! command -v $cmd >/dev/null 2>&1; then
        echo "Error: Required command '$cmd' not found. Please install it first."
        if [ "$cmd" = "parallel" ]; then
            echo "To install GNU Parallel:"
            echo "  On macOS: brew install parallel"
            echo "  On Ubuntu/Debian: sudo apt-get install parallel"
        fi
        exit 1
    fi
done

# If it's the first time running GNU Parallel, accept its citation notice
parallel --citation >/dev/null 2>&1 || true

# Count total number of files
TOTAL_FILES=$(find "$SOURCE_DIR" -type f -iname "*.pdf" | wc -l)
echo "Found $TOTAL_FILES PDF files to process"
echo "Starting parallel PDF compression using up to 8 jobs..."

# Process files in parallel with proper null-handling
find "$SOURCE_DIR" -type f -iname "*.pdf" -print0 | \
    parallel --null --bar -j 8 --line-buffer compress_pdf {}

echo "PDF compression process completed."
