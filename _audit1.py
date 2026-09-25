import sys, asyncio, json
sys.path.insert(0, '.')
import main as m, test_local as tl

UID = "AUD1"

async def fresh():
    p = tl.make_plugin()
    pl = m._default_player(UID)
    await p._save_player(pl)
    return p

async def show(p, uid=UID):
    pl = await p._load_player(uid)
    return {
        "items": dict(pl.get("items") or {}),
        "limited_uses": dict(pl.get("limited_uses") or {}),
        "baits": {k: v for k, v in (pl.get("baits") or {}).items() if v},
        "rods": pl.get("rods"),
        "equipped_rod": pl.get("equipped_rod"),
        "gold": pl.get("gold"),
        "limited_rod_casts": pl.get("limited_rod_casts"),
    }

async def main():
    p = await fresh()
    ev = tl.FakeEvent(UID)
    print("=== items in config with uses>0 ===")
    for iid in p._limited_item_list():
        print("  ", iid, p.items[iid])
    print("=== _item_list (shop) ===", p._item_list())
    print("=== _bait_list (shop) ===", p._bait_list())

    await p._save_player(m._default_player(UID))
    out = await tl.cmd(p, ev, '道具', '买', '潮汐竿·凭证')
    print("[买 凭证]", tl.text_of(out))
    print("  after:", await show(p))

    await p._save_player(m._default_player(UID))
    out = await tl.cmd(p, ev, '道具', '买', 'tide_rod_pass', '999')
    print("[买 凭证 x999]", tl.text_of(out))
    st = await show(p)
    print("  after:", st)
    pl = await p._load_player(UID)
    print("  _limited_item_left:", p._limited_item_left(pl, "tide_rod_pass"))
    print("  _active_limited_rod:", (p._active_limited_rod(pl) or {}).get("id"))

    await p._save_player(m._default_player(UID))
    out = await tl.cmd(p, ev, '道具', '买', '深渊秘饵·凭证')
    print("[买 深渊秘饵·凭证]", tl.text_of(out))
    print("  after:", await show(p))

    await p._save_player(m._default_player(UID))
    out = await tl.cmd(p, ev, '买', '潮汐竿·凭证')
    print("[钓鱼 买 凭证]", tl.text_of(out))

    await p._save_player(m._default_player(UID))
    out = await tl.cmd(p, ev, '鱼竿', '买', '潮汐竿')
    print("[鱼竿 买 潮汐竿]", tl.text_of(out))
    out = await tl.cmd(p, ev, '鱼竿')
    print("[鱼竿 列表]", tl.text_of(out)[:600])

    await p._save_player(m._default_player(UID))
    out = await tl.cmd(p, ev, '道具')
    print("[道具 列表]", tl.text_of(out)[:900])
    out = await tl.cmd(p, ev, '商店')
    print("[商店]", tl.text_of(out)[:600])
    out = await tl.cmd(p, ev, '鱼饵')
    print("[鱼饵 列表]", tl.text_of(out)[:600])

asyncio.run(main())
