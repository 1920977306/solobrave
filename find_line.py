with open('js/ui-chat-simple.js', 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    if 'await AI.sendMessage' in line:
        print(f'Found at line {i+1}')
        for j in range(max(0, i-5), min(len(lines), i+20)):
            print(f'{j+1}: {repr(lines[j])}')
        break
