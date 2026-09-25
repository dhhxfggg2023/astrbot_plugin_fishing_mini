# -*- coding: utf-8 -*-
"""审计探针（临时文件，跑完删除）。"""
import asyncio
import sys

sys.path.insert(0, '.')
import main as m  # noqa: E402
import test_local as tl  # noqa: E402

GOLD = 10 ** 9


async def fresh(uid, gold=GOLD):
    p = tl.make_plugin()
    pl = m._default_player(uid)
    pl['gold'] = gold
    await p._save_player(pl)
    return p, await p._load_player(uid)


async def cmd_of(p, uid, *words):
    out = await tl.cmd(p, tl.FakeEvent(uid), *words)
    pl = await p._load_player(uid)
    return tl.text_of(out), pl


def sec(t):
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)


async def main():
    # ---------- 0. 出厂配置里「限定」条目到底有哪些 ----------
    sec("0 出厂数据：限定条目清单")
    p, _ = await fresh('U000')
    print("限用道具（uses>0）:", p._limited_item_list())
    print("商店可见道具 _item_list:", p._item_list())
    print("商店可见饵 _bait_list:", p._bait_list())
    print("全部饵:", list(p.baits))
    print("全部道具:", list(p.items))
    for iid in p._limited_item_list():
        spec = p.items[iid]
        print(f"  {iid}: price={spec['price']} uses={spec['uses']} rod={spec.get('rod')!r} bait={spec.get('bait')!r} unlock={spec['unlock_level']}")
    for bid in ("abyss_secret", "vip_bait"):
        b = p.baits[bid]
        print(f"  bait {bid}: price={b['price']} special={b.get('special')} unlock={b['unlock_level']} 在货架={bid in p._bait_list()}")
    for rod in p.rods:
        if p._rod_is_limited(rod):
            print(f"  rod {rod['id']}: price={rod['price']} unlock={rod['unlock_level']} limited={p._rod_is_limited(rod)}")

    # ---------- 1. 道具店：限用凭证能不能买 ----------
    sec("1 道具店 /钓鱼 道具 买 <限用凭证>")
    for uid, name in (('U101', '潮汐竿·凭证'), ('U102', 'tide_rod_pass'),
                      ('U103', '深渊秘饵·凭证'), ('U104', '贵客饵·凭证'),
                      ('U105', '星陨竿·凭证'), ('U106', '瞬手竿·凭证'),
                      ('U107', '双尾竿·凭证')):
        p, _ = await fresh(uid)
        txt, pl = await cmd_of(p, uid, '道具', '买', name)
        delta = pl['gold'] - GOLD
        items = {k: v for k, v in (pl.get('items') or {}).items() if v}
        print(f"[{name}] 扣钱={delta} items={items}")
        print("   回复:", txt.replace("\n", " ⏎ ")[:150])

    # ---------- 2. 鱼饵店：限定饵能不能买 ----------
    sec("2 鱼饵店 /钓鱼 鱼饵 买 <限定饵>")
    for uid, name in (('U201', '深渊秘饵'), ('U202', 'abyss_secret'),
                      ('U203', '贵客饵'), ('U204', 'vip_bait')):
        p, _ = await fresh(uid)
        txt, pl = await cmd_of(p, uid, '鱼饵', '买', name)
        delta = pl['gold'] - GOLD
        print(f"[{name}] 扣钱={delta} baits={pl.get('baits')} equipped={pl.get('equipped_bait')}")
        print("   回复:", txt.replace("\n", " ⏎ ")[:150])

    # ---------- 3. 智能买 /钓鱼 买 <名字> ----------
    sec("3 智能买 /钓鱼 买 <名字>")
    for uid, name in (('U301', '潮汐竿·凭证'), ('U302', '深渊秘饵'), ('U303', '贵客饵·凭证'),
                      ('U304', '潮汐竿'), ('U305', '仙露')):
        p, _ = await fresh(uid)
        txt, pl = await cmd_of(p, uid, '买', name)
        delta = pl['gold'] - GOLD
        print(f"[{name}] 扣钱={delta} items={ {k:v for k,v in (pl.get('items') or {}).items() if v} } "
              f"baits={ {k:v for k,v in (pl.get('baits') or {}).items() if v} } rods={pl.get('rods')}")
        print("   回复:", txt.replace("\n", " ⏎ ")[:150])

    # ---------- 4. 买了到底生不生效 ----------
    sec("4 买到手后是否真的生效（权限/特权）")
    p, _ = await fresh('U401')
    txt, pl = await cmd_of(p, 'U401', '道具', '买', '潮汐竿·凭证')
    pl = await p._load_player('U401')
    print("买入后 items:", pl.get('items'), "limited_uses:", pl.get('limited_uses'))
    print("_active_limited_rod:", (p._active_limited_rod(pl) or {}).get('id'))
    print("_rod() 当前生效竿:", p._rod(pl)['id'], "（玩家 rods=）", pl.get('rods'))
    print("_limited_item_left:", p._limited_item_left(pl, 'tide_rod_pass'))
    print("_limited_use_text:", p._limited_use_text(pl, 'tide_rod_pass'))
    # 重复购买叠加
    for _ in range(3):
        await cmd_of(p, 'U401', '道具', '买', '潮汐竿·凭证', '500')
    pl = await p._load_player('U401')
    print("再买 3×500 后 张数:", (pl.get('items') or {}).get('tide_rod_pass'),
          " 剩余次数:", p._limited_item_left(pl, 'tide_rod_pass'),
          " 扣钱:", pl['gold'] - GOLD)

    p, _ = await fresh('U402')
    txt, pl = await cmd_of(p, 'U402', '鱼饵', '买', '深渊秘饵', '50')
    pl = await p._load_player('U402')
    print("买入限定饵后 baits:", pl.get('baits'), "equipped:", pl.get('equipped_bait'))
    print("_bait_is_limited('abyss_secret'):", p._bait_is_limited('abyss_secret'))
    print("_bait_list 里有没有:", 'abyss_secret' in p._bait_list())
    # 看这一竿到底用没用它
    baits_before = dict(pl.get('baits') or {})
    pl['last_fish_time'] = 0
    await p._save_player(pl)
    out = await tl.cast(p, tl.FakeEvent('U402'))
    pl2 = await p._load_player('U402')
    print("抛竿前 baits:", baits_before, " 抛竿后:", pl2.get('baits'))
    print("抛竿回复首行:", tl.text_of(out).splitlines()[0][:100])

    # ---------- 5. 数量 / 负数 / 超大 ----------
    sec("5 数量参数：负数 / 0 / 超大 / 小数")
    for uid, words in (
        ('U501', ('道具', '买', '仙露', '-5')),
        ('U502', ('道具', '买', '仙露', '0')),
        ('U503', ('道具', '买', '仙露', '999999999')),
        ('U504', ('道具', '买', '仙露', 'abc')),
        ('U505', ('道具', '买', '仙露', '-99999999999999999999')),
        ('U506', ('道具', '买', '潮汐竿·凭证', '-5')),
        ('U507', ('鱼饵', '买', '蚯蚓', '-5')),
        ('U508', ('鱼饵', '买', '蚯蚓', '999999999')),
        ('U509', ('道具', '买', '仙露', '99999999999999999999999999')),
        ('U510', ('道具', '买', '仙露', '1e3')),
    ):
        p, _ = await fresh(uid)
        txt, pl = await cmd_of(p, uid, *words)
        got = (pl.get('items') or {}).get('仙露') or (pl.get('items') or {}).get('tide_rod_pass') \
            or (pl.get('baits') or {}).get('worm')
        print(f"[{' '.join(words)}] 扣钱={pl['gold'] - GOLD} 到手={got}")
        print("   回复:", txt.replace("\n", " ⏎ ")[:150])

    # ---------- 6. 等级门槛对限定条目 ----------
    sec("6 等级门槛（限定凭证 vs 对应竿本身）")
    p, _ = await fresh('U601')
    pl = await p._load_player('U601')
    print("1 级玩家 _player_level:", m._player_level(pl))
    for iid in p._limited_item_list():
        print(f"  {iid} 的拒绝文案: {p._unlock_refuse_text(pl, p.items[iid])!r}  unlock_level={p.items[iid]['unlock_level']}")
    for bid in ("abyss_secret", "vip_bait"):
        print(f"  {bid} 的拒绝文案: {p._unlock_refuse_text(pl, p.baits[bid])!r}  unlock_level={p.baits[bid]['unlock_level']}")
    print("  对应竿 tide_rod 的拒绝文案:", p._unlock_refuse_text(pl, p.rod_by_id['tide_rod']))
    # 1 级玩家直接买凭证 → 直接用潮汐竿
    p, _ = await fresh('U602')
    txt, _ = await cmd_of(p, 'U602', '道具', '买', '潮汐竿·凭证')
    pl = await p._load_player('U602')
    print("1 级玩家买 0 元凭证后生效竿:", p._rod(pl)['id'], " rods:", pl['rods'])

    # ---------- 7. 卖 / 丢弃 ----------
    sec("7 卖 / 丢弃限用凭证")
    p, _ = await fresh('U701')
    pl = await p._load_player('U701')
    pl['items'] = {'tide_rod_pass': 3}
    await p._save_player(pl)
    for words in (('卖', '全部'), ('卖', '潮汐竿·凭证'), ('道具', '卖', '潮汐竿·凭证'),
                  ('卖', '道具'), ('丢弃', '潮汐竿·凭证'), ('道具', '丢弃', '潮汐竿·凭证')):
        pl = await p._load_player('U701')
        g0 = pl['gold']
        txt, pl2 = await cmd_of(p, 'U701', *words)
        print(f"[{' '.join(words)}] 钱变化={pl2['gold']-g0} items={pl2.get('items')}")
        print("   回复:", txt.replace("\n", " ⏎ ")[:130])

    # ---------- 8. 存档写失败 → 半买 ----------
    sec("8 保存失败时：钱扣了没 / 货发了没")
    p, _ = await fresh('U801')
    orig_save = p._save_player

    async def bad_save(player, *a, **kw):
        return False

    p._save_player = bad_save
    try:
        ev = tl.FakeEvent('U801')
        out = await tl.cmd(p, ev, '道具', '买', '仙露', '2')
        print("回复:", tl.text_of(out).replace("\n", " ⏎ ")[:200])
    finally:
        p._save_player = orig_save
    pl = await p._load_player('U801')
    print("（磁盘上）钱:", pl['gold'] - GOLD, " items:", pl.get('items'))

    # ---------- 9. 编辑器桥：item_defs 能不能被改成可买 ----------
    sec("9 编辑器：把 uses 改成 0 / 删掉整行")
    raw = m.DEFAULTS['item_defs']
    edited = [ln for ln in raw]
    for idx, ln in enumerate(edited):
        if ln.startswith('tide_rod_pass|'):
            parts = ln.split('|')
            print("原始行段数:", len(parts), parts)
            parts[8] = '0'                     # uses -> 0
            edited[idx] = '|'.join(parts)
    parsed = m._parse_item_defs(edited)
    vis = m._shop_visible_items(parsed)
    print("uses=0 后 tide_rod_pass 在商店可见表里？", 'tide_rod_pass' in vis)
    print("uses=0 后 _rod_is_limited(tide_rod) =", None)
    # 真起一个插件，改配置
    cfg = dict(tl._CFG)
    cfg['item_defs'] = edited
    p2 = tl.make_plugin(cfg)
    print("改配置后 tide_rod_pass spec:", p2.items['tide_rod_pass'])
    print("改配置后 _item_list 含它？", 'tide_rod_pass' in p2._item_list())
    print("改配置后 _rod_is_limited(tide_rod) =", p2._rod_is_limited(p2.rod_by_id['tide_rod']))
    pl = m._default_player('U901')
    pl['gold'] = GOLD
    await p2._save_player(pl)
    txt, pl2 = await cmd_of(p2, 'U901', '道具', '买', '潮汐竿·凭证', '3')
    print("uses=0 时买 3 个:", txt.replace("\n", " ⏎ ")[:150])
    print("  到手:", pl2.get('items'), " 剩余可用次数:", p2._limited_item_left(pl2, 'tide_rod_pass'))
    print("  当前生效竿:", p2._rod(pl2)['id'])

    # 删掉整个 tide_rod_pass 行呢？
    cfg2 = dict(tl._CFG)
    cfg2['item_defs'] = [ln for ln in raw if not ln.startswith('tide_rod_pass|')]
    p3 = tl.make_plugin(cfg2)
    print("删行后 _rod_is_limited(tide_rod) =", p3._rod_is_limited(p3.rod_by_id['tide_rod']),
          " special=", p3.rod_by_id['tide_rod'].get('special'), " uses=", p3.rod_by_id['tide_rod'].get('uses'))
    pl = m._default_player('U902')
    pl['gold'] = GOLD
    await p3._save_player(pl)
    txt, pl2 = await cmd_of(p3, 'U902', '鱼竿', '买', '潮汐竿')
    print("删行后 /钓鱼 鱼竿 买 潮汐竿 ->", txt.replace("\n", " ⏎ ")[:160])
    print("  rods:", pl2.get('rods'), " 扣钱:", pl2['gold'] - GOLD)

    # 删掉竿表第 11 段 uses 呢（旧的半截行）
    cfg3 = dict(tl._CFG)
    cfg3['rod_defs'] = [
        ln for ln in m.DEFAULTS['rod_defs'] if not ln.startswith('tide_rod|')
    ] + ["tide_rod|潮汐竿|🌊|0|0.10|0.08|1|异色猎手|0.10|0.95"]
    p4 = tl.make_plugin(cfg3)
    print("半截行 _rod_is_limited(tide_rod) =", p4._rod_is_limited(p4.rod_by_id['tide_rod']))
    pl = m._default_player('U903')
    pl['gold'] = GOLD
    await p4._save_player(pl)
    txt, pl2 = await cmd_of(p4, 'U903', '鱼竿', '买', '潮汐竿')
    print("半截行 /钓鱼 鱼竿 买 潮汐竿 ->", txt.replace("\n", " ⏎ ")[:160])
    print("  rods:", pl2.get('rods'), " 扣钱:", pl2['gold'] - GOLD)


asyncio.run(main())
