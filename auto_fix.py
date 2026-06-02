import re

with open('office-v3.html','r',encoding='utf-8') as f:
    content = f.read()

# Fix pattern 1: "<tag attr="" -> '<tag attr="'
# Fix pattern 2: "" data-channel="" -> '" data-channel="'
# General rule: if a double-quoted string contains "", change outer quotes to single quotes

def fix_broken_strings(text):
    """Fix strings that have unescaped double quotes inside double-quoted strings."""
    result = []
    i = 0
    while i < len(text):
        if text[i] == '"':
            # Find the end of this double-quoted string
            j = i + 1
            while j < len(text):
                if text[j] == '\\':
                    j += 2
                elif text[j] == '"':
                    break
                else:
                    j += 1
            
            if j < len(text):
                # Check if the next character after closing " is also "
                # This indicates a broken pattern like "text""more"
                if j + 1 < len(text) and text[j + 1] == '"' and text[j - 1] != '\\':
                    # This is part of a broken multi-string pattern
                    # Find the full expression
                    # Look for pattern: "..." + ... + "..."
                    # Or: "...""..." (two strings concatenated without +)
                    
                    # For now, just collect and we'll process later
                    result.append(text[i:j+1])
                    i = j + 1
                else:
                    result.append(text[i:j+1])
                    i = j + 1
            else:
                result.append(text[i])
                i += 1
        else:
            result.append(text[i])
            i += 1
    
    return ''.join(result)

# Better approach: find all lines with "" inside JS strings and fix them
lines = content.split('\n')
fixed_lines = []
for line in lines:
    original = line
    # Fix pattern: return "<div class="channel-tab" + ...
    # Should be: return '<div class="channel-tab"' + ...
    
    # Find all occurrences of pattern: = "<...""..."  or  return "<...""..."
    # These have HTML attributes with quotes inside double-quoted strings
    
    # Simple regex to find broken patterns
    # Pattern: (="[^"]*""[^"]*")  - double quote inside double-quoted string
    
    # Replace specific broken patterns
    # 1. "<tag attr="" + var + "" attr2="" + var2 + "">
    #    -> '<tag attr="' + var + '" attr2="' + var2 + '">'
    
    # Use a more targeted approach
    # Find all double-quoted strings that contain "" (two consecutive quotes)
    
    def replacer(match):
        # This is a double-quoted string
        inner = match.group(1)
        # Check if inner contains "" which indicates a broken pattern
        if '""' in inner:
            # This shouldn't happen with our regex, but just in case
            pass
        return match.group(0)
    
    # Find pattern like: "text""more" (two strings without operator)
    # And fix by changing outer quotes or adding + between
    
    # Actually, let's just fix the specific patterns we know are broken
    # Pattern 1: value="" -> value=\" (escaped quotes inside double-quoted string)
    # But we changed outer to single quotes instead
    
    fixed_lines.append(line)

content = '\n'.join(fixed_lines)

# Write back
with open('office-v3.html','w',encoding='utf-8') as f:
    f.write(content)

print('Done')
