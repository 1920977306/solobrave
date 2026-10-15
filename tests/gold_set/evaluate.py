#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gold set 回归评测 (feat/vision-gold-set 2026-10-15)

对 tests/gold_set/ 里每张真实截图跑完整生产管线
(_call_kimi_vision → _vision_qa_pipeline witness+常识+grounding+修复闭环),
与同名 *.expected.json 人工标注逐字段比对。

用途: 每次改提取/校验/修复逻辑后手动跑一遍, 数值字段 regress 立刻暴露。

用法 (在仓库根目录):
    SOLO_BRAVE_DB_SOURCE=/Users/qichen/solobrave-prod/data/solobrave.db \
      python3 tests/gold_set/evaluate.py
    python3 tests/gold_set/evaluate.py --only li_tinger_2103

退出码: 0 = 全部字段命中; 1 = 有偏差或管线异常。
注意: 会真实调用 kimi vision + glm witness/修复 (每次约 1 分钟/张), 不进 pytest 全量。
"""
import sys, os, json, base64, argparse, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)  # solobrave-server 依赖 douyin_parser 等同目录模块


def _load_srv():
    spec = importlib.util.spec_from_file_location('srv', os.path.join(ROOT, 'solobrave-server.py'))
    srv = importlib.util.module_from_spec(spec)
    sys.modules['srv'] = srv
    spec.loader.exec_module(srv)
    return srv


def _norm_str(v):
    return str(v).strip().replace('，', ',').replace(' ', '')


def _to_float(v):
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(',', '').replace('¥', '').replace('%', '').strip()
    try:
        return float(s)
    except ValueError:
        return None


def compare(expected, actual, prefix=''):
    """逐字段比对, 返回 (oks, misses) 两个 list[(path, exp, act)]。"""
    oks, misses = [], []
    for k, exp in expected.items():
        if k.startswith('_'):
            continue
        path = f'{prefix}{k}'
        act = (actual or {}).get(k, '<缺失>')
        if isinstance(exp, dict) and isinstance(actual, dict) and isinstance(actual.get(k), dict):
            sub_ok, sub_miss = compare(exp, actual[k], prefix=path + '.')
            oks += sub_ok
            misses += sub_miss
            continue
        ef, af = _to_float(exp), _to_float(act)
        if ef is not None and af is not None:
            if abs(ef - af) <= max(0.01, abs(ef) * 0.001):
                oks.append((path, exp, act))
            else:
                misses.append((path, exp, act))
        elif _norm_str(exp) == _norm_str(act):
            oks.append((path, exp, act))
        elif _norm_str(act) in ('null', 'none', '<缺失>') and exp is None:
            oks.append((path, exp, act))
        else:
            misses.append((path, exp, act))
    return oks, misses


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', help='只跑指定 case 名(不带扩展名)')
    args = ap.parse_args()

    srv = _load_srv()
    cases = []
    for f in sorted(os.listdir(HERE)):
        if f.endswith('.expected.json'):
            name = f[:-len('.expected.json')]
            png = os.path.join(HERE, name + '.png')
            if os.path.exists(png):
                cases.append((name, png, os.path.join(HERE, f)))
    if args.only:
        cases = [c for c in cases if c[0] == args.only]
    if not cases:
        print('没有找到 gold case (需要 同名.png + .expected.json 成对)')
        return 1

    total_ok = total_miss = 0
    failed_cases = []
    for name, png, exp_path in cases:
        print(f'\n=== {name} ===', flush=True)
        expected = json.load(open(exp_path))
        b64 = base64.b64encode(open(png, 'rb').read()).decode()
        try:
            raw = srv._call_kimi_vision(b64, agent_id='emp_gold_eval', role='商务')
            if not raw:
                print('  ✗ Pass1 vision 无输出')
                failed_cases.append(name)
                continue
            out = srv._vision_qa_pipeline(raw, b64, '商务')
            actual = srv._parse_vision_json(out)
            if actual is None:
                print('  ✗ 闭环输出解析失败')
                failed_cases.append(name)
                continue
        except Exception as e:
            print(f'  ✗ 管线异常: {type(e).__name__}: {e}')
            failed_cases.append(name)
            continue
        oks, misses = compare(expected, actual)
        for path, exp, act in oks:
            print(f'  ✓ {path}: {act!r}')
        for path, exp, act in misses:
            print(f'  ✗ {path}: 期望 {exp!r} 实际 {act!r}')
        total_ok += len(oks)
        total_miss += len(misses)
        if misses:
            failed_cases.append(name)

    print(f'\n========== 汇总: {total_ok} 命中 / {total_miss} 偏差 ==========')
    if failed_cases:
        print('偏差 case: ' + ', '.join(failed_cases))
        return 1
    print('全部命中 ✅')
    return 0


if __name__ == '__main__':
    sys.exit(main())
