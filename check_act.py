import sys
lines = sys.stdin.readlines()
for i, line in enumerate(lines):
    if 'activitiesFallback' in line:
        print('L' + str(i+1) + ': ' + line.rstrip()[:100])
        for j in range(i+1, min(i+20, len(lines))):
            print('L' + str(j+1) + ': ' + lines[j].rstrip()[:100])
        break
