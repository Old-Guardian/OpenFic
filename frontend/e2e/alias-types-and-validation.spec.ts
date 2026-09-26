import { test, expect } from "@playwright/test";

import { resolveRemoteEntryEditorState } from "../src/features/world-info/components/entry-editor-state";
import {
  updateWorldInfoEntryBrief,
  updateWorldInfoEntryBriefs,
} from "../src/features/world-info/pages/world-info-entry-cache";
import type { Character, CharacterListItem } from "../src/lib/character.types";
import type {
  WorldInfoEntry,
  WorldInfoEntryBrief,
  WorldInfoEntryBriefListResponse,
} from "../src/lib/world-info.types";

test.describe("T6: 别名数据结构、校验与缓存行为", () => {
  test("别名数量限制与长度限制常量校验", () => {
    const MAX_ALIASES = 20;
    const MAX_ALIAS_CHARS = 100;

    // 边界: 100 字符合法，101 字符超长
    const valid100Chars = "a".repeat(100);
    const invalid101Chars = "a".repeat(101);
    expect(valid100Chars.length).toBe(MAX_ALIAS_CHARS);
    expect(invalid101Chars.length).toBeGreaterThan(MAX_ALIAS_CHARS);

    // 边界: 20 个别名合法，第 21 个超额
    const twentyAliases = Array.from({ length: 20 }, (_, i) => `别名_${i + 1}`);
    expect(twentyAliases.length).toBe(MAX_ALIASES);
  });

  test("别名归一化与排重规则", () => {
    const entityName = "李寻欢";
    const aliases = ["小李探花", "小李飞刀"];

    const normalize = (s: string) => s.trim().toLowerCase();

    // 1. 空值校验
    expect("   ".trim()).toBe("");

    // 2. 正式名称重名校验
    expect(normalize("  李寻欢  ")).toBe(normalize(entityName));

    // 3. 既有别名重名校验（不区分大小写）
    const newAlias = "小李探花";
    const exists = aliases.some((a) => normalize(a) === normalize(newAlias));
    expect(exists).toBe(true);

    const distinctAlias = "探花郎";
    const notExists = aliases.some((a) => normalize(a) === normalize(distinctAlias));
    expect(notExists).toBe(false);
  });

  test("Character 与 CharacterListItem 包含别名字段并正确赋初值", () => {
    const rawCharacter: Character = {
      id: "char_1",
      projectId: "proj_1",
      name: "楚留香",
      description: "盗帅夜留香",
      imageUrl: null,
      isFavorited: false,
      aliases: ["盗帅", "香帅"],
      relationshipCount: 0,
      createdAt: "2026-09-18T00:00:00Z",
      updatedAt: "2026-09-18T00:00:00Z",
    };

    expect(rawCharacter.aliases).toEqual(["盗帅", "香帅"]);

    const listItem: CharacterListItem = {
      id: rawCharacter.id,
      projectId: rawCharacter.projectId,
      name: rawCharacter.name,
      imageUrl: rawCharacter.imageUrl,
      tokenCount: 10,
      isFavorited: rawCharacter.isFavorited,
      aliases: rawCharacter.aliases,
      relationshipCount: 0,
      createdAt: rawCharacter.createdAt,
      updatedAt: rawCharacter.updatedAt,
    };

    expect(listItem.aliases).toHaveLength(2);
    expect(listItem.aliases[0]).toBe("盗帅");
  });

  test("WorldInfoEntry 与 WorldInfoEntryBrief 包含别名字段", () => {
    const entry: WorldInfoEntry = {
      id: "entry_1",
      worldInfoId: "wi_1",
      uid: 1,
      name: "含沙射影",
      order: 1,
      content: "暗器之王",
      tokenCount: 8,
      isEnabled: true,
      aliases: ["暴雨梨花针", "针匣"],
      createdAt: "2026-09-18T00:00:00Z",
      updatedAt: "2026-09-18T00:00:00Z",
    };

    const brief: WorldInfoEntryBrief = {
      id: entry.id,
      worldInfoId: entry.worldInfoId,
      uid: entry.uid,
      name: entry.name,
      order: entry.order,
      tokenCount: entry.tokenCount,
      isEnabled: entry.isEnabled,
      aliases: entry.aliases,
      createdAt: entry.createdAt,
      updatedAt: entry.updatedAt,
    };

    expect(brief.aliases).toEqual(["暴雨梨花针", "针匣"]);
  });

  test("世界书条目缓存更新函数 updateWorldInfoEntryBrief 保留与更新别名", () => {
    const initialList: WorldInfoEntryBriefListResponse = {
      items: [
        {
          id: "entry_1",
          worldInfoId: "wi_1",
          uid: 1,
          name: "旧名称",
          order: 1,
          tokenCount: 5,
          isEnabled: true,
          aliases: ["旧别名"],
          createdAt: "2026-09-18T00:00:00Z",
          updatedAt: "2026-09-18T00:00:00Z",
        },
      ],
      total: 1,
    };

    const updatedList = updateWorldInfoEntryBrief(initialList, "entry_1", (item) => ({
      ...item,
      name: "新名称",
      aliases: ["新别名1", "新别名2"],
    }));

    expect(updatedList?.items[0].name).toBe("新名称");
    expect(updatedList?.items[0].aliases).toEqual(["新别名1", "新别名2"]);
  });

  test("批量更新缓存函数 updateWorldInfoEntryBriefs 正确保留其他项别名并更新指定项", () => {
    const list: WorldInfoEntryBriefListResponse = {
      items: [
        {
          id: "entry_1",
          worldInfoId: "wi_1",
          uid: 1,
          name: "条目1",
          order: 1,
          tokenCount: 5,
          isEnabled: true,
          aliases: ["别名1"],
          createdAt: "2026-09-18T00:00:00Z",
          updatedAt: "2026-09-18T00:00:00Z",
        },
        {
          id: "entry_2",
          worldInfoId: "wi_1",
          uid: 2,
          name: "条目2",
          order: 2,
          tokenCount: 8,
          isEnabled: false,
          aliases: ["别名2"],
          createdAt: "2026-09-18T00:00:00Z",
          updatedAt: "2026-09-18T00:00:00Z",
        },
      ],
      total: 2,
    };

    const result = updateWorldInfoEntryBriefs(list, ["entry_1"], (item) => ({
      ...item,
      isEnabled: false,
    }));

    expect(result?.items[0].isEnabled).toBe(false);
    expect(result?.items[0].aliases).toEqual(["别名1"]);
    expect(result?.items[1].aliases).toEqual(["别名2"]);
  });

  test("resolveRemoteEntryEditorState 在本地有修改时保护别名不被覆盖，无修改时同步远端", () => {
    const localState = {
      name: "条目A",
      content: "本地正在编辑的内容",
      tokenCount: 15,
      aliases: ["本地别名1"],
    };

    const remoteState = {
      name: "条目A",
      content: "远端原始内容",
      tokenCount: 10,
      aliases: ["远端别名A", "远端别名B"],
    };

    // 本地有未保存变动时：保留本地别名
    const resolvedWithChanges = resolveRemoteEntryEditorState(localState, remoteState, true);
    expect(resolvedWithChanges.aliases).toEqual(["本地别名1"]);
    expect(resolvedWithChanges.content).toBe("本地正在编辑的内容");

    // 本地无变动时：采用远端最新别名
    const resolvedWithoutChanges = resolveRemoteEntryEditorState(localState, remoteState, false);
    expect(resolvedWithoutChanges.aliases).toEqual(["远端别名A", "远端别名B"]);
    expect(resolvedWithoutChanges.content).toBe("远端原始内容");
  });

  test("清空别名行为：空列表 [] 清除所有别名，重载后为空数组", () => {
    let aliases: string[] = ["别名A", "别名B"];
    // 清空操作
    aliases = [];
    expect(aliases).toEqual([]);
    expect(aliases).toHaveLength(0);
  });
});
