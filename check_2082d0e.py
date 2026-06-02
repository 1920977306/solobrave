import sys
lines = sys.stdin.readlines()
for i, line in enumerate(lines):
    if 'activitiesFallback.map' in line:
        print('HTML L' + str(i+1) + ': ' + line.rstrip())
        for j in range(i, min(i+15, len(lines))):
            print('  L' + str(j+1) + ': ' + lines[j].rstrip()[:100])
        break
