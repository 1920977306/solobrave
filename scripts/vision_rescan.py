#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
★ fix/ocr-canonical-sync-v4 (2026-09-25): P1.1 vision OCR 重跑实测脚本.

老大方案 (李婶儿第一张图验收后 v4 工单):
  "用李婶儿原图在当前 vision 模型上重跑 N 次 (同图重复 3-5 次):
   量化: 9 个汇总字段错几个 / 小数错几个 / 同一数字多次结果是否稳定 (一致性).
   区分: 是'系统性读错'还是'随机抖动'.
   同图跑第二模型做交叉对比."

执行 (Mac 端 / 妍妍):
  python3 scripts/vision_rescan.py \\
      --input /Users/qichen/solobrave-prod/ocr_dump_李婶儿.txt \\
      --runs 5 \\
      --output /tmp/vision_rescan_report.json \\
      --md /tmp/vision_rescan_report.md

输出:
  - JSON 报告: 字段级错误率 + 一致性矩阵 + 两模型交叉对比
  - Markdown 报告: 人读摘要 (字段错误率排序 + 系统性错字段列表)

⚠️ 只读脚本: 不写 DB / data/. 输出报告到 /tmp (不入 git).
⚠️ vision_rescan_single 函数留 TODO stub — 实际环境由妍妍填 vision API 调用.
   Mini Windows 无 vision API key, 写 stub. 妍妍 Mac 端填实际 API 实现.
"""
import argparse
import json
import sys
import time
from pathlib import Path
from collections import defaultdict


# ============================================================
# ★ TODO: 实际环境由妍妍填 vision API 调用
# ============================================================

def vision_rescan_single(image_path, model='current'):
    """单次 vision OCR 重跑. 调用现有 vision 模块 / API.

    Args:
        image_path: 原图路径 (李婶儿原图 jpg/png)
        model: 模型名 ('current' 当前默认 / 'gpt-4o' 第二模型交叉对比)

    Returns:
        dict 字段名 → 值. 结构同 ocr_raw_fields (李婶儿 dump 顶层字段)
        例: {'total_gmv': '¥100万-500万', 'product_count': 23, ...}

    ★ 实际环境由妍妍填:
      - 可 import server 端 vision 模块 (如有)
      - 或独立调 vision API (kimi / openai / 等)
      - 环境变量 VISION_API_KEY 配密钥
      - 返回结构必须跟 ground_truth 的字段名对齐
    """
    raise NotImplementedError(
        'vision_rescan_single 需填实际 vision API 调用 (Mini Windows 无 API key). '
        'Mac 端妍妍填实际实现: import server 端 vision 模块或独立调 API.'
    )


# ============================================================
# 错误率统计
# ============================================================

def compute_field_stats(runs, ground_truth):
    """计算字段级错误率 + 一致性 + 系统性标记.

    Args:
        runs: list[dict] 多次重跑结果
        ground_truth: dict 真值对照 (李婶儿原图 9 字段 + 14 字段)

    Returns:
        dict 字段名 → {
            'runs': [...],         # 各次跑的值
            'truth': ...,            # 真值
            'error_count': N,        # 几次跑错
            'error_rate': 0.6,       # 60% 错
            'consistent': True/False,# 多跑结果是否一致
            'systematic': True/False,# 是否系统性错 (>= 3 次错同一个错值)
            'all_wrong_values': [...], # 错值列表 (去重)
        }
    """
    field_stats = {}
    all_keys = set(ground_truth.keys())
    for run in runs:
        if isinstance(run, dict):
            all_keys.update(run.keys())

    for key in all_keys:
        truth = ground_truth.get(key)
        values = [r.get(key) if isinstance(r, dict) else None for r in runs]
        # 错误次数 (跟真值不等且非空)
        error_count = sum(1 for v in values if v is not None and v != truth)
        # 一致性 (多次值是否相同, 排除 None)
        non_null = [v for v in values if v is not None]
        consistent = len(set(non_null)) <= 1
        # 系统性错 (多次都错同一个错值, >= 3 次)
        if non_null and truth is not None:
            wrong_values = [v for v in non_null if v != truth]
            all_wrong_unique = list(set(wrong_values))
            systematic = len(all_wrong_unique) == 1 and len(wrong_values) >= 3
        else:
            all_wrong_unique = list(set(non_null))
            systematic = False

        field_stats[key] = {
            'runs': values,
            'truth': truth,
            'error_count': error_count,
            'error_rate': error_count / len(values) if values else 0,
            'consistent': consistent,
            'systematic': systematic,
            'all_wrong_values': all_wrong_unique,
        }

    return field_stats


def cross_model_compare(runs_a, runs_b):
    """第二模型交叉对比. 量化两模型差异.

    Returns:
        dict 字段名 → {
            'model_a_avg_error': 0.4,
            'model_b_avg_error': 0.6,
            'both_wrong': True/False,   # 两模型都错
            'disagreement_rate': 0.3,    # 不同意率
        }
    """
    # 计算两模型各自的众数 / 平均
    def _majority(values):
        non_null = [v for v in values if v is not None]
        if not non_null:
            return None
        # 简化: 取第一个非 None (实际场景可用 Counter 算众数)
        return non_null[0]

    compare = {}
    all_keys = set()
    for run in runs_a + runs_b:
        if isinstance(run, dict):
            all_keys.update(run.keys())

    for key in all_keys:
        a_values = [r.get(key) if isinstance(r, dict) else None for r in runs_a]
        b_values = [r.get(key) if isinstance(r, dict) else None for r in runs_b]
        a_majority = _majority(a_values)
        b_majority = _majority(b_values)
        # 两模型不一致次数
        a_disagreements = sum(1 for v in a_values if v is not None and v != a_majority)
        b_disagreements = sum(1 for v in b_values if v is not None and v != b_majority)
        # 两模型结果不一致 (a_majority != b_majority)
        disagreement = a_majority != b_majority

        compare[key] = {
            'model_a_majority': a_majority,
            'model_b_majority': b_majority,
            'model_a_disagreement_count': a_disagreements,
            'model_b_disagreement_count': b_disagreements,
            'two_models_disagree': disagreement,
        }

    return compare


# ============================================================
# Markdown 报告生成
# ============================================================

def render_markdown_report(report):
    """生成 Markdown 摘要报告 (人读)."""
    lines = [
        f'# Vision OCR Rescan Report',
        '',
        f'**生成时间**: {time.strftime("%Y-%m-%d %H:%M:%S")}',
        f'**输入 OCR dump**: `{report["input_path"]}`',
        f'**单模型重跑次数**: {report["runs"]}',
        f'**模型 A**: {report["model_a"]}',
        f'**模型 B**: {report["model_b"]}',
        '',
        '## 模型 A 字段错误率 (按错误率降序)',
        '',
    ]

    # 模型 A 错误率排序
    stats_a = report['model_a_stats']
    sorted_fields_a = sorted(
        stats_a.items(),
        key=lambda x: (-x[1]['error_rate'], x[0])
    )

    lines.append('| 字段 | 真值 | 错误率 | 一致性 | 系统性错 | 错值 |')
    lines.append('|---|---|---|---|---|---|')
    for field, stat in sorted_fields_a:
        if stat['error_rate'] == 0:
            continue  # 跳过全对的字段
        consistent_str = '✓' if stat['consistent'] else '✗'
        systematic_str = '✗ 是' if stat['systematic'] else '✓ 否'
        wrong_str = ', '.join(str(v) for v in stat['all_wrong_values'][:3])
        truth_str = str(stat['truth'])
        if len(truth_str) > 20:
            truth_str = truth_str[:17] + '...'
        lines.append(
            f'| {field} | {truth_str} | {stat["error_rate"]*100:.0f}% '
            f'| {consistent_str} | {systematic_str} | {wrong_str} |'
        )

    lines.append('')
    lines.append('## 两模型交叉对比 (差异字段)')
    lines.append('')
    lines.append('| 字段 | 模型 A 众数 | 模型 B 众数 | 两模型差异 |')
    lines.append('|---|---|---|---|')
    cross = report['cross_model']
    for field, comp in cross.items():
        if not comp['two_models_disagree']:
            continue
        lines.append(
            f'| {field} | {comp["model_a_majority"]} | {comp["model_b_majority"]} | ✗ |'
        )

    lines.append('')
    lines.append('## 结论')
    lines.append('')
    systematic_fields = [f for f, s in stats_a.items() if s['systematic']]
    if systematic_fields:
        lines.append(f'- 系统性错字段 (>= 3 次错同一值): **{", ".join(systematic_fields)}**')
        lines.append('- 建议: P1.2 阶段针对这些字段改提取方式 (裁剪放大/多模型一致/换模型)')
    else:
        lines.append('- 未发现系统性错字段, 错误多为随机抖动')
    inconsistent_fields = [f for f, s in stats_a.items() if not s['consistent']]
    if inconsistent_fields:
        lines.append(f'- 一致性差字段 (多次结果不同): **{", ".join(inconsistent_fields)}**')

    return '\n'.join(lines)


# ============================================================
# 李婶儿原图真值 (老大方案 v4 工单)
# ============================================================

# ★ 实际跑前由妍妍填李婶儿原图真值 (按 v4 工单对账表 9 + 14 字段)
#   示例占位: 实际数字以原图为准
LI_SHEN_ER_GROUND_TRUTH = {
    # 9 个汇总字段 (v4 工单表 A)
    'product_count': 25,           # 实际: 李婶儿原图
    'total_shops': 23,
    'total_history_days': 490,
    'live_ratio': 20.57,
    'live_sessions': 8,
    'live_views': '2.62万',
    'video_ratio': 76.8,
    'video_count': 32,
    'video_plays': 2734,
    # 14 字段 (v4 工单表 B)
    'single_video_settlement': '¥5,000-2万',
    'total_gmv': '¥100万-500万',
    'video_gpm': '300',
    'avg_live_gmv': '¥100万-500万',
    'live_gpm': '500-1,000',
    # 类目 + 城市 (防塌缩测试)
    # 'category_distribution': {...},  # dict 字段, 单独比较
    # 'fan_city_tier': {...},         # dict 字段, 单独比较
}


# ============================================================
# main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description='★ fix/ocr-canonical-sync-v4: P1.1 vision OCR 重跑实测脚本 (只读, 输出 JSON + Markdown)'
    )
    parser.add_argument('--input', required=True, help='李婶儿 OCR dump JSON 路径 (输入)')
    parser.add_argument('--runs', type=int, default=5, help='单模型重跑次数 (默认 5)')
    parser.add_argument(
        '--output', default='/tmp/vision_rescan_report.json',
        help='JSON 输出路径 (默认 /tmp/vision_rescan_report.json)'
    )
    parser.add_argument(
        '--md', default='/tmp/vision_rescan_report.md',
        help='Markdown 输出路径 (默认 /tmp/vision_rescan_report.md)'
    )
    parser.add_argument('--model-a', default='current', help='模型 A (默认 current)')
    parser.add_argument('--model-b', default='gpt-4o', help='模型 B (默认 gpt-4o)')
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f'ERROR: 输入文件不存在: {input_path}', file=sys.stderr)
        sys.exit(1)

    # 读李婶儿 OCR dump
    print(f'[vision_rescan] 读 OCR dump: {input_path}')
    with open(input_path, 'r', encoding='utf-8') as f:
        dump = json.load(f)

    # ★ 实际跑前由 vision_rescan_single 实现 - Mini Windows 抛 NotImplementedError
    print(f'[vision_rescan] 模型 A ({args.model_a}) 重跑 {args.runs} 次...')
    try:
        runs_a = [vision_rescan_single(str(input_path), args.model_a) for _ in range(args.runs)]
    except NotImplementedError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        print('→ Mac 端妍妍填实际 vision API 调用后重跑', file=sys.stderr)
        sys.exit(2)
    print(f'[vision_rescan] 模型 B ({args.model_b}) 重跑 {args.runs} 次...')
    runs_b = [vision_rescan_single(str(input_path), args.model_b) for _ in range(args.runs)]

    # 错误率统计
    print('[vision_rescan] 计算字段错误率...')
    stats_a = compute_field_stats(runs_a, LI_SHEN_ER_GROUND_TRUTH)
    stats_b = compute_field_stats(runs_b, LI_SHEN_ER_GROUND_TRUTH)

    # 两模型交叉对比
    print('[vision_rescan] 两模型交叉对比...')
    cross = cross_model_compare(runs_a, runs_b)

    # 汇总报告
    report = {
        'input_path': str(input_path),
        'runs': args.runs,
        'model_a': args.model_a,
        'model_b': args.model_b,
        'ground_truth': LI_SHEN_ER_GROUND_TRUTH,
        'model_a_runs': runs_a,
        'model_b_runs': runs_b,
        'model_a_stats': stats_a,
        'model_b_stats': stats_b,
        'cross_model': cross,
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }

    # JSON 输出
    out_json = Path(args.output)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f'[vision_rescan] JSON 报告已写入: {out_json}')

    # Markdown 输出
    md_content = render_markdown_report(report)
    out_md = Path(args.md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    with open(out_md, 'w', encoding='utf-8') as f:
        f.write(md_content)
    print(f'[vision_rescan] Markdown 报告已写入: {out_md}')

    # stdout 摘要
    print('\n=== 摘要 ===')
    print(f'模型 A 错误率排序 (top 5):')
    sorted_a = sorted(stats_a.items(), key=lambda x: (-x[1]['error_rate'], x[0]))[:5]
    for field, stat in sorted_a:
        if stat['error_rate'] > 0:
            print(f'  {field}: {stat["error_rate"]*100:.0f}% ({stat["error_count"]}/{args.runs})')


if __name__ == '__main__':
    main()