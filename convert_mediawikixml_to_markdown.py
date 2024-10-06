import os
import re
import xml.etree.ElementTree as ET
import subprocess
import argparse
import traceback

class CleanLink:
    def __init__(self, flatten: bool, meta: dict):
        self.flatten = flatten
        self.meta = meta

    def clean_link(self, match):
        link_to_clean = match.group(1)

        # If the link starts with http we have a malformed Wiki link. Return Broken link.
        if re.match(r"^https?://", link_to_clean):
            return f'[{link_to_clean}]'

        # Convert relative paths to absolute paths
        if re.match(r'^\.*?/', link_to_clean):
            link_to_clean = f"{self.meta['url']}/{link_to_clean}"

        if '|' not in link_to_clean:
            link = link_to_clean
            link_text = link_to_clean
        else:
            link, link_text = link_to_clean.split('|', 1)

        # Normalize path
        link = self.normalize_path(link.strip())

        # Flat file structure - replace / with _
        if self.flatten:
            link = link.replace('/', '_')

        # Cleanup remaining artifacts
        link = link.replace(' ', '_')
        link_text = link_text.strip()

        return f"[[{link}|{link_text}]]"

    def normalize_path(self, path: str) -> str:
        parts = []
        path = path.replace('\\', '/')
        path = re.sub(r'/+', '/', path)
        segments = path.split('/')

        for segment in segments:
            if segment == '.':
                continue
            elif segment == '..':
                if parts:
                    parts.pop()
            else:
                parts.append(segment)

        return '/'.join(parts)


class PandocFix:
    @staticmethod
    def url_fix(url: str) -> str:
        return url.replace("=&", "=%20&").replace("= ", "=%20 ").replace(".&", ".%20&")


class Convert:
    def __init__(self, options: dict):
        self.filename = options.get('filename')
        self.output = options.get('output', './output/')
        self.flatten = options.get('flatten', False)
        self.addmeta = options.get('addmeta', False)
        self.indexes = options.get('indexes', False)
        self.skiperrors = options.get('skiperrors', False)
        self.format = options.get('format', 'gfm')
        self.counter = 0
        self.directory_list = []
        self.data_to_convert = []
        self.page_list = ''

        self.pandoc_installed = self.check_pandoc_installed()
        self.pandoc_version = self.get_pandoc_version()

    def run(self):
        self.create_directory(self.output)
        self.load_data(self.load_file())
        self.convert_data()
        self.rename_files()
        print(f"{self.counter} files converted")

    def check_pandoc_installed(self):
        try:
            subprocess.run(["pandoc", "--version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return True
        except subprocess.CalledProcessError:
            raise Exception("Pandoc is not installed or not found in PATH.")

    def get_pandoc_version(self):
        result = subprocess.run(["pandoc", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result.stdout.decode().splitlines()[0]

    def convert_data(self):
        # Define the namespace (you might need to adjust it based on the XML structure)
        namespace = {'mw': 'http://www.mediawiki.org/xml/export-0.11/'}

        for page in self.data_to_convert:
            title_element = page.find("mw:title", namespace)

            if title_element is None or title_element.text is None:
                print(f"Warning: No title found for a page. Skipping page.")
                continue  # Skip this page if no title is found

            title = title_element.text
            text_element = page.find(".//mw:revision/mw:text", namespace)

            if text_element is None or text_element.text is None:
                print(f"Warning: No text content for page '{title}'. Skipping cleaning.")
                continue  # Skip this page if there is no text to process

            text = text_element.text
            file_meta = self.retrieve_file_info(title)
            cleaned_text = self.clean_text(text, file_meta)

            try:
                converted_text = self.run_pandoc(cleaned_text)
                output = self.get_metadata(file_meta) + converted_text
                self.save_file(file_meta, output)
                self.counter += 1
            except Exception as e:
                if not self.skiperrors:
                    raise Exception(f"Error converting {file_meta['title']}: {e}")
                else:
                    print(f"Failed converting {file_meta['title']}: {e}")

    def clean_text(self, text, file_meta):
        clean_linker = CleanLink(self.flatten, file_meta)
        pandoc_fix = PandocFix()

        # Replace encoded HTML entities for < and >
        text = re.sub(r'&lt;', '<', text)
        text = re.sub(r'&gt;', '>', text)

        # Improved template detection
        is_template = False
        if "<noinclude>" in text and "{{" in text and "}}" in text:
            is_template = True

        if is_template:
            # Template-specific cleaning
            # Remove <noinclude> and <includeonly> tags, but keep their content
            text = re.sub(r'</?noinclude>', '', text)
            text = re.sub(r'</?includeonly>', '', text)

            # Preserve template structure, but remove extra curly braces
            text = re.sub(r'{{{', '{', text)
            text = re.sub(r'}}}', '}', text)

            # Remove any remaining curly braces that aren't part of the template structure
            text = re.sub(r'(?<!\{)\{(?!\{)|(?<!\})\}(?!\})', '', text)
        else:
            # Non-template cleaning
            # Improved table cleaning
            def clean_table_start(match):
                attrs = match.group(1)
                # Remove all attributes except class
                cleaned_attrs = re.findall(r'class="[^"]*"', attrs)
                if cleaned_attrs:
                    return '{| ' + ' '.join(cleaned_attrs)
                else:
                    return '{|'

            text = re.sub(r'{\|(.*?)\n', clean_table_start, text)

            # Remove unnecessary table attributes
            text = re.sub(r'\|\s*cellspacing="[^"]+"', '', text)
            text = re.sub(r'\|\s*cellpadding="[^"]+"', '', text)
            text = re.sub(r'\|\s*border="[^"]+"', '', text)
            text = re.sub(r'\|\s*style="[^"]+"', '', text)
            text = re.sub(r'\|\s*width="[^"]+"', '', text)
            text = re.sub(r'\|\s*align="[^"]+"', '', text)
            text = re.sub(r'\|\s*summary="[^"]+"', '', text)

            # Handle nested curly braces and other non-standard syntax
            text = re.sub(r'{{{([^}]+)}}+', r'\1', text)  # Remove triple (or more) curly braces
            text = re.sub(r'{{([^}]+)}}', r'\1', text)  # Remove double curly braces
            text = re.sub(r'{([^|{}]+)}', r'\1', text)  # Remove single curly braces (except for tables)

            # Balance table tags and remove excessive closing tags
            open_tables = 0
            lines = text.split('\n')
            cleaned_lines = []
            for line in lines:
                if line.strip().startswith('{|'):
                    open_tables += 1
                    cleaned_lines.append('{|')
                elif line.strip().startswith('|}'):
                    if open_tables > 0:
                        open_tables -= 1
                        cleaned_lines.append('|}')
                elif line.strip().startswith('{|}'):
                    # Remove malformed table-like structures
                    continue
                else:
                    cleaned_lines.append(line)
            
            # Close any remaining open tables
            cleaned_lines.extend(['|}'] * open_tables)
            
            text = '\n'.join(cleaned_lines)

            # Remove any remaining excessive closing tags
            text = re.sub(r'\|}\s*}+', '|}', text)

            # Handle problematic row starts
            text = re.sub(r'^[-!].*scope="row".*$', '', text, flags=re.MULTILINE)  # Remove problematic scope rows
            text = re.sub(r'^[-!]', '|', text, flags=re.MULTILINE)  # Replace any remaining "-" or "!" at start of lines with "|"

            # Further clean table syntax
            text = re.sub(r'\|\s*\n', '|\n', text)  # Remove trailing spaces from row lines
            text = re.sub(r'\|-+', '|-', text)  # Normalize row separators
            text = re.sub(r'\|-\s*\|', '|-', text)  # Remove erroneous pipe symbol between row separators
            text = re.sub(r'\|\-\s+', '|-', text)  # Remove extra spaces after row separators

            # Remove unnecessary <br> tags in tables
            text = re.sub(r'<br\s*/?>', '', text)

            # Ensure table cells are on separate lines
            text = re.sub(r'\|([^\n\|]+)\|', r'|\n\1\n|', text)

            # Remove empty table rows
            text = re.sub(r'\|-\s*\|-', '|-', text)

            # Remove any remaining "|" at the start or end of lines (outside of table context)
            text = re.sub(r'(?<!\{)\|\s*$', '', text, flags=re.MULTILINE)
            text = re.sub(r'^\|\s*(?!\})', '', text, flags=re.MULTILINE)

            # Convert MediaWiki tables to Markdown tables
            text = self.convert_tables_to_markdown(text)

        # Hack to fix URLs for older version of Pandoc
        if self.pandoc_version <= '2.0.2':
            text = re.sub(r'\[(http.+?)\]', lambda m: pandoc_fix.url_fix(m.group(0)), text)

        # Clean up links
        text = re.sub(r'\[\[(.+?)\]\]', lambda m: clean_linker.clean_link(m), text)

        # Add logging
        #print("Template detection result:", is_template)
        #print("Cleaned content (first 200 chars):")
        #print(text[:200])

        return text, is_template
    def convert_tables_to_markdown(self, text):
        def convert_table(match):
            table_content = match.group(1)
            rows = table_content.split('|-')
            markdown_rows = []
            
            for i, row in enumerate(rows):
                cells = row.split('|')
                cleaned_cells = [cell.strip() for cell in cells if cell.strip()]
                if cleaned_cells:
                    markdown_row = '| ' + ' | '.join(cleaned_cells) + ' |'
                    markdown_rows.append(markdown_row)
                    
                    # Add header separator after the first row
                    if i == 0:
                        separator = '|' + '|'.join(['---' for _ in cleaned_cells]) + '|'
                        markdown_rows.append(separator)
            
            return '\n'.join(markdown_rows)

        # Convert MediaWiki tables to Markdown tables
        text = re.sub(r'{\|(.*?)\|}', convert_table, text, flags=re.DOTALL)
        return text

    def convert_data(self):
        namespace = {'mw': 'http://www.mediawiki.org/xml/export-0.11/'}

        for page in self.data_to_convert:
            title_element = page.find("mw:title", namespace)

            if title_element is None or title_element.text is None:
                print(f"Warning: No title found for a page. Skipping page.")
                continue

            title = title_element.text
            text_element = page.find(".//mw:revision/mw:text", namespace)

            if text_element is None or text_element.text is None:
                print(f"Warning: No text content for page '{title}'. Skipping cleaning.")
                continue

            text = text_element.text
            file_meta = self.retrieve_file_info(title)

            try:
                # print(f"Processing: {file_meta['title']}")
                #print("Raw content (first 200 chars):")
                #print(text[:200])

                cleaned_text, is_template = self.clean_text(text, file_meta)
                
                #print("Cleaned content (first 200 chars):")
                #print(cleaned_text[:200])

                if is_template:
                    #print("Converting template to Markdown")
                    converted_text = self.convert_template_to_markdown(cleaned_text)
                else:
                    #print("Converting with Pandoc")
                    converted_text = self.run_pandoc(cleaned_text)
                
                #print("Converted content (first 200 chars):")
                #print(converted_text[:200])

                output = self.get_metadata(file_meta) + converted_text
                self.save_file(file_meta, output)
                self.counter += 1
            except Exception as e:
                print(f"Error converting {file_meta['title']}:")
                print(traceback.format_exc())
                if not self.skiperrors:
                    raise
                else:
                    print(f"Skipping {file_meta['title']} due to error")


    def convert_template_to_markdown(self, text):
        lines = text.split('\n')
        markdown_lines = ['# Template Documentation\n']
        in_pre = False
        in_template = False
        template_params = []

        for line in lines:
            stripped_line = line.strip()
            if stripped_line.startswith('This is the'):
                markdown_lines.append(f"## {stripped_line}\n")
            elif stripped_line.startswith('<pre>'):
                in_pre = True
                markdown_lines.append("## Template Usage\n```")
            elif stripped_line.startswith('</pre>'):
                in_pre = False
                markdown_lines.append("```\n")
            elif stripped_line.startswith('{{'):
                in_template = True
                markdown_lines.append(stripped_line)
            elif stripped_line.startswith('}}'):
                in_template = False
                markdown_lines.append(stripped_line)
            elif in_template and '=' in stripped_line:
                param, value = stripped_line.split('=', 1)
                template_params.append(param.strip('| '))
                markdown_lines.append(stripped_line)
            elif stripped_line:
                markdown_lines.append(stripped_line)

        if template_params:
            markdown_lines.append("\n## Template Parameters")
            for param in template_params:
                markdown_lines.append(f"- `{param}`")

        # Add logging
        print("Template conversion result (first 200 chars):")
        print('\n'.join(markdown_lines)[:200])

        return '\n'.join(markdown_lines)

    def run_pandoc(self, text):
        result = subprocess.run(["pandoc", "-f", "mediawiki", "-t", self.format], input=text.encode(),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            print("Pandoc error output:")
            print(result.stderr.decode())
            raise Exception(result.stderr.decode())
        return result.stdout.decode()

    def save_file(self, file_meta, text):
        directory = file_meta['directory']
        self.create_directory(directory)
        file_path = os.path.join(directory, f"{file_meta['filename']}.md")

        try:
            # Ensure file is written with UTF-8 encoding to handle all Unicode characters
            with open(file_path, 'w', encoding='utf-8') as file:
                file.write(text)
            print(f"Converted: {file_path}")
        except UnicodeEncodeError as e:
            raise Exception(f"Error saving file {file_meta['title']}: {e}")


    def retrieve_file_info(self, title: str):
        # Replace invalid characters for Windows file system, such as : and / and *
        invalid_chars = {
            ':': '_',
            '/': '_',
            '*': '_',
            '?': '_',
            '<': '_',
            '>': '_',
            '|': '_',
            '\\': '_',
            '"': '_'
        }
        
        # Replace invalid characters in the title
        for char, replacement in invalid_chars.items():
            title = title.replace(char, replacement)

        url = title.replace(' ', '_')
        filename = url
        directory = ''

        if '/' in url:
            parts = url.split('/')
            directory = '/'.join(parts[:-1])
            filename = parts[-1]
            self.directory_list.append(directory)

        directory = os.path.join(self.output, directory)
        return {'directory': directory, 'filename': filename, 'title': title, 'url': url}



    def create_directory(self, directory):
        if directory and not os.path.exists(directory):
            os.makedirs(directory)

    def get_metadata(self, file_meta):
        if self.addmeta:
            return f"---\ntitle: {file_meta['title']}\npermalink: /{file_meta['url']}/\n---\n\n"
        return ''

    def load_file(self):
        if not os.path.exists(self.filename):
            raise Exception(f"File {self.filename} does not exist.")

        # Use utf-8 encoding to open the file
        with open(self.filename, 'r', encoding='utf-8') as file:
            return file.read()

    def load_data(self, xml_data):
        try:
            root = ET.fromstring(xml_data)

            # Register the namespace used in the XML
            namespaces = {'mw': 'http://www.mediawiki.org/xml/export-0.11/'}

            # Try to find all <page> elements using the namespace
            self.data_to_convert = root.findall('.//mw:page', namespaces)

            if not self.data_to_convert:
                # If no pages are found, list all element tags for debugging
                all_elements = [elem.tag for elem in root.iter()]
                print(f"Available elements in the XML: {all_elements}")
                raise Exception("No pages found in XML data.")
            else:
                print(f"Found {len(self.data_to_convert)} <page> elements.")
        except ET.ParseError as e:
            raise Exception(f"Error parsing XML: {e}")

    def rename_files(self):
        if not self.flatten and self.indexes:
            for directory in self.directory_list:
                file_path = os.path.join(self.output, directory + '.md')
                if os.path.exists(file_path):
                    os.rename(file_path, os.path.join(self.output, directory, 'index.md'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Convert MediaWiki XML to Markdown (GFM format).")
    parser.add_argument('filename', type=str, help="Path to the MediaWiki XML file")
    parser.add_argument('--output', type=str, default='./output/', help="Output directory for converted files")
    parser.add_argument('--flatten', action='store_true', help="Flatten file structure")
    parser.add_argument('--addmeta', action='store_true', help="Add permalink metadata")
    parser.add_argument('--format', type=str, default='gfm', help="Conversion format (default: gfm)")
    parser.add_argument('--indexes', action='store_true', help="Use index.md for directories")
    parser.add_argument('--skiperrors', action='store_true', help="Skip errors during conversion")
    
    args = parser.parse_args()

    options = {
        'filename': args.filename,
        'output': args.output,
        'flatten': args.flatten,
        'addmeta': args.addmeta,
        'format': args.format,
        'indexes': args.indexes,
        'skiperrors': args.skiperrors,
    }
    
    converter = Convert(options)
    converter.run()

#python convert_mediawikixml_to_markdown.py ./full_eln_dump.xml --output ./output/ --flatten --addmeta
