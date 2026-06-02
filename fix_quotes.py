import re

with open('office-v3.html','r',encoding='utf-8') as f:
    content = f.read()

# Fix strings that have "" inside double-quoted strings
# Pattern like: "<option value="" + (x) + "">"
# Should be: '<option value="' + (x) + '">'
# Or we can escape: "<option value=\"" + (x) + "\">"

# Simple approach: change outer quotes from " to ' for strings containing inner ""
def fix_string_quotes(text):
    # Find all double-quoted strings that contain "" (two consecutive quotes)
    # These are likely broken template literal conversions
    
    # Pattern: "...""..."  (string containing two consecutive quotes)
    # We need to change outer " to '
    
    result = []
    i = 0
    while i < len(text):
        if text[i] == '"':
            # Find end of string
            j = i + 1
            while j < len(text):
                if text[j] == '\\':
                    j += 2
                elif text[j] == '"':
                    break
                else:
                    j += 1
            
            if j < len(text):
                string_content = text[i+1:j]
                # Check if this string contains "" (which would be a broken pattern)
                # Actually we need to check if the NEXT char after closing " is also "
                if j + 1 < len(text) and text[j+1] == '"':
                    # This is a "" pattern inside a double-quoted string context
                    # Change outer quotes to single quotes
                    # But we need to handle the entire expression, not just one string
                    pass
                
                result.append(text[i:j+1])
                i = j + 1
            else:
                result.append(text[i])
                i += 1
        else:
            result.append(text[i])
            i += 1
    
    return ''.join(result)

# Better approach: use regex to find and fix specific broken patterns
# The broken pattern is: "...""..." where the middle "" should be escaped or outer quotes changed

# Let's find all occurrences of "" in the content and fix them
lines = content.split('\n')
fixed_lines = []
for line in lines:
    # Fix pattern: "...text""more..."  ->  '...text"more...'
    # This is when a template literal like `text"more` was converted to "text""more"
    
    # Simple fix: if line contains '""' inside what looks like a JS string assignment,
    # change the outer quotes to single quotes
    
    # Check if line has a pattern like: = "...""..."
    if re.search(r'=\s*"[^"]*""', line):
        # Change outer " to '
        # Find the string boundaries
        match = re.search(r'(=\s*)"([^"]*)""([^"]*)"', line)
        if match:
            prefix = match.group(1)
            part1 = match.group(2)
            part2 = match.group(3)
            line = line[:match.start()] + prefix + "'" + part1 + '"' + part2 + "'" + line[match.end():]
    
    fixed_lines.append(line)

content = '\n'.join(fixed_lines)

# Write back
with open('office-v3.html','w',encoding='utf-8') as f:
    f.write(content)

print('Fixed quote issues')
