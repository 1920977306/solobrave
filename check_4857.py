import sys
content = sys.stdin.read()
lines = content.split('\n')
if len(lines) > 4856:
    line = lines[4856]
    print('2082d0e L4857:', repr(line))
    print('Length:', len(line))
    for i in range(max(0, 4856-5), min(len(lines), 4856+10)):
        marker = '>>> ' if i == 4856 else '    '
        print(marker + 'L' + str(i+1) + ': ' + lines[i].rstrip()[:100])
