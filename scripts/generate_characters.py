#!/usr/bin/env python3
"""扩展现有 characters.json，为所有人物补充 RPG 属性，并新增大量人物。"""

import json
import random

random.seed(42)

with open("content/characters.json", "r", encoding="utf-8") as f:
    data = json.load(f)

# 为现有人物补充 RPG 属性
def assign_rpg(c):
    # 基于现有属性推导 RPG 属性
    ability = c.get("ability", 50)
    integrity = c.get("integrity", 50)
    courage = c.get("courage", 50)
    loyalty = c.get("loyalty", 50)
    style = c.get("style", "")
    office = c.get("office", "")
    faction = c.get("faction", "")
    personal_skills = c.get("personal_skills", [])

    # 武力：武将/边镇/军队派系高，文官低
    force = ability
    if any(k in office for k in ["总兵", "参将", "游击", "守备", "督师", "经略", "武将", "边镇", "将领"]):
        force = random.randint(70, 95)
    elif any(k in office for k in ["内阁", "尚书", "侍郎", "翰林", "御史"]):
        force = random.randint(35, 60)
    elif faction == "军队":
        force = random.randint(65, 90)
    elif any(s in str(personal_skills) for s in ["骑射", "兵法", "武艺", "冲锋", "奇袭", "悍勇"]):
        force = random.randint(70, 95)
    else:
        force = random.randint(40, 75)

    # 智谋：ability 为基础，文官/谋士高
    wisdom = ability
    if any(k in office for k in ["内阁", "尚书", "侍郎", "翰林", "御史", "谋士", "军师"]):
        wisdom = random.randint(70, 95)
    elif any(s in str(personal_skills) for s in ["谋略", "计策", "智计", "洞察", "策划", "权谋"]):
        wisdom = random.randint(75, 95)
    else:
        wisdom = random.randint(45, 80)

    # 魅力：courage + integrity 综合，外交人物高
    charm = round((courage + integrity) / 2)
    if any(s in str(personal_skills) for s in ["说服", "感召", "外交", "舆情", "清流", "书法"]):
        charm = random.randint(65, 90)
    else:
        charm = random.randint(40, 75)

    # 气运：随机，但重要历史人物偏高
    luck = random.randint(35, 80)

    # 修为：默认0，特殊人物有修为（道士、江湖人士、奇人）
    cultivation = 0
    if any(k in style for k in ["道", "仙", "隐", "奇", "侠", "异"]):
        cultivation = random.randint(10, 60)
    if any(s in str(personal_skills) for s in ["道术", "武功", "剑法", "内功", "奇门", "遁甲", "符咒"]):
        cultivation = random.randint(15, 70)

    # HP
    max_hp = 80 + force // 2 + cultivation // 3
    hp = max_hp

    c["force"] = force
    c["wisdom"] = wisdom
    c["charm"] = charm
    c["luck"] = luck
    c["cultivation"] = cultivation
    c["hp"] = hp
    c["max_hp"] = max_hp
    c["exp"] = 0
    c["level"] = 1 + force // 40 + wisdom // 40 + cultivation // 30
    return c

for c in data["characters"]:
    assign_rpg(c)

# ── 新增人物池 ──
NEW_CHARACTERS = [
    # 江湖奇人
    {"name": "武当张真人", "office": "武当山掌教", "office_type": "待铨", "faction": "中立", "aliases": ["张真人", "武当掌门"], "personal_skills": ["太极拳剑", "内家心法", "奇门遁甲"], "loyalty": 60, "ability": 88, "integrity": 90, "courage": 85, "style": "仙风道骨", "birth_year": 1550, "historical_death_year": 0, "summary": "武当掌教，传闻已活过百岁，深居简出，偶尔下山济世。", "power_id": "ming", "force": 92, "wisdom": 95, "cultivation": 85},
    {"name": "铁臂周侗", "office": "江湖武师", "office_type": "待铨", "faction": "中立", "aliases": ["周师傅", "铁臂翁"], "personal_skills": ["枪棒无双", "调教弟子", "江湖人脉"], "loyalty": 55, "ability": 78, "integrity": 72, "courage": 88, "style": "刚正不阿", "birth_year": 1565, "historical_death_year": 0, "summary": "江湖上赫赫有名的武师，传闻曾指点过多位军中将领武艺。", "power_id": "ming", "force": 94, "wisdom": 70, "cultivation": 25},
    {"name": "青莲剑客", "office": "江湖游侠", "office_type": "待铨", "faction": "中立", "aliases": ["青莲", "李剑客"], "personal_skills": ["剑术通神", "诗酒风流", "易容术"], "loyalty": 45, "ability": 82, "integrity": 60, "courage": 90, "style": "放荡不羁", "birth_year": 1580, "historical_death_year": 0, "summary": "来历不明的剑客，剑法如青莲绽放，嗜酒如命，行踪飘忽。", "power_id": "ming", "force": 91, "wisdom": 75, "cultivation": 45},
    {"name": "玄机道长", "office": "龙虎山天师府法师", "office_type": "待铨", "faction": "中立", "aliases": ["玄机", "张法师"], "personal_skills": ["符咒道法", "观星测运", "炼丹制药"], "loyalty": 58, "ability": 85, "integrity": 80, "courage": 65, "style": "神秘莫测", "birth_year": 1575, "historical_death_year": 0, "summary": "龙虎山高道，精通符箓丹道，据说能观天象而知国运。", "power_id": "ming", "force": 55, "wisdom": 92, "cultivation": 78},
    {"name": "毒手药王", "office": "江湖神医", "office_type": "待铨", "faction": "中立", "aliases": ["药王", "孙神医"], "personal_skills": ["医毒双绝", "炼丹秘术", "起死回生"], "loyalty": 40, "ability": 90, "integrity": 50, "courage": 55, "style": "亦正亦邪", "birth_year": 1560, "historical_death_year": 0, "summary": "江湖上人称毒手药王，既能起死回生，也能杀人无形。", "power_id": "ming", "force": 60, "wisdom": 95, "cultivation": 55},
    {"name": "红衣女侠", "office": "江湖侠女", "office_type": "待铨", "faction": "中立", "aliases": ["红衣", "红娘子"], "personal_skills": ["轻功卓绝", "暗器百步", "侠义心肠"], "loyalty": 50, "ability": 76, "integrity": 70, "courage": 92, "style": "嫉恶如仇", "birth_year": 1600, "historical_death_year": 0, "summary": "江湖上令人闻风丧胆的女侠，专管不平事，来去如风。", "power_id": "ming", "force": 85, "wisdom": 68, "cultivation": 30},
    {"name": "盲眼琴师", "office": "宫廷乐师", "office_type": "翰林院", "faction": "中立", "aliases": ["琴师", "瞎子阿炳"], "personal_skills": ["琴音摄魂", "听风辨位", "占卜吉凶"], "loyalty": 65, "ability": 80, "integrity": 75, "courage": 50, "style": "孤高冷傲", "birth_year": 1585, "historical_death_year": 0, "summary": "双目失明却琴艺通神的乐师，据说琴音能扰人心神、预知祸福。", "power_id": "ming", "force": 40, "wisdom": 88, "cultivation": 40},
    {"name": "鬼面商人", "office": "西域商人", "office_type": "待铨", "faction": "中立", "aliases": ["鬼面", "胡商"], "personal_skills": ["奇货可居", "丝路情报", "机关暗器"], "loyalty": 35, "ability": 82, "integrity": 30, "courage": 60, "style": "唯利是图", "birth_year": 1580, "historical_death_year": 0, "summary": "常年戴面具的西域商人，贩卖奇珍异宝，消息灵通得可怕。", "power_id": "ming", "force": 55, "wisdom": 85, "cultivation": 20},
    {"name": "隐剑山庄主", "office": "隐剑山庄庄主", "office_type": "待铨", "faction": "中立", "aliases": ["庄主", "叶庄主"], "personal_skills": ["铸剑秘术", "剑阵合击", "藏兵于民"], "loyalty": 55, "ability": 84, "integrity": 78, "courage": 80, "style": "沉稳内敛", "birth_year": 1568, "historical_death_year": 0, "summary": "隐剑山庄主人，世代铸剑，暗中豢养死士，势力遍布江南。", "power_id": "ming", "force": 82, "wisdom": 80, "cultivation": 35},
    {"name": "白眉老僧", "office": "少林寺达摩院长老", "office_type": "待铨", "faction": "中立", "aliases": ["白眉", "空见大师"], "personal_skills": ["金刚不坏", "禅宗心法", "伏魔杖法"], "loyalty": 60, "ability": 86, "integrity": 95, "courage": 75, "style": "慈悲为怀", "birth_year": 1545, "historical_death_year": 0, "summary": "少林寺百岁高僧，武功深不可测，对天下大势有独到见解。", "power_id": "ming", "force": 90, "wisdom": 90, "cultivation": 70},

    # 边镇猛将
    {"name": "曹文诏", "office": "山西总兵", "office_type": "边镇", "faction": "军队", "aliases": ["曹总兵", "曹将军"], "personal_skills": ["骑兵突袭", "以少胜多", "铁血治军"], "loyalty": 72, "ability": 84, "integrity": 70, "courage": 95, "style": "刚烈勇猛", "birth_year": 1585, "historical_death_year": 1635, "summary": "明末第一猛将，骑兵战术无双，对流寇作战极其凶狠。", "power_id": "ming", "force": 93, "wisdom": 68},
    {"name": "曹变蛟", "office": "辽东副将", "office_type": "边镇", "faction": "军队", "aliases": ["曹副将", "变蛟"], "personal_skills": ["冲锋陷阵", "夜袭奇兵", "死战不退"], "loyalty": 75, "ability": 80, "integrity": 65, "courage": 96, "style": "悍不畏死", "birth_year": 1590, "historical_death_year": 1642, "summary": "曹文诏之侄，与其叔并称曹家双猛，作战风格更为激进。", "power_id": "ming", "force": 92, "wisdom": 62},
    {"name": "左良玉", "office": "平贼将军", "office_type": "边镇", "faction": "军队", "aliases": ["左帅", "左将军"], "personal_skills": ["拥兵自重", "纵横中原", "察言观色"], "loyalty": 40, "ability": 78, "integrity": 35, "courage": 70, "style": "枭雄本色", "birth_year": 1599, "historical_death_year": 1645, "summary": "拥兵八十万的平贼将军，实力雄厚却难以驾驭。", "power_id": "ming", "force": 80, "wisdom": 72},
    {"name": "黄得功", "office": "靖南伯", "office_type": "边镇", "faction": "军队", "aliases": ["黄伯爷", "得功"], "personal_skills": ["忠勇无双", "箭术神准", "护主心切"], "loyalty": 88, "ability": 76, "integrity": 75, "courage": 94, "style": "忠肝义胆", "birth_year": 1590, "historical_death_year": 1645, "summary": "明末忠将，对皇室忠心耿耿，个人战力极高。", "power_id": "ming", "force": 91, "wisdom": 60},
    {"name": "刘泽清", "office": "山东总兵", "office_type": "边镇", "faction": "军队", "aliases": ["刘总兵"], "personal_skills": ["保存实力", "见风使舵", "割据自保"], "loyalty": 30, "ability": 65, "integrity": 25, "courage": 45, "style": "滑头投机", "birth_year": 1590, "historical_death_year": 1649, "summary": "山东军阀，反复无常，以保存自身实力为第一要务。", "power_id": "ming", "force": 68, "wisdom": 55},
    {"name": "高杰", "office": "兴平伯", "office_type": "边镇", "faction": "军队", "aliases": ["高伯爷", "翻山鹞"], "personal_skills": ["流寇出身", "骑兵游击", "悍勇难制"], "loyalty": 45, "ability": 72, "integrity": 30, "courage": 88, "style": "桀骜不驯", "birth_year": 1585, "historical_death_year": 1645, "summary": "原李自成部将，降明后封为伯，凶猛难制。", "power_id": "ming", "force": 87, "wisdom": 50},
    {"name": "周遇吉", "office": "山西总兵", "office_type": "边镇", "faction": "军队", "aliases": ["周总兵"], "personal_skills": ["死守孤城", "步战精湛", "宁死不屈"], "loyalty": 85, "ability": 78, "integrity": 80, "courage": 95, "style": "铁血忠烈", "birth_year": 1590, "historical_death_year": 1644, "summary": "宁武关总兵，李自成攻宁武时血战至死，满门殉国。", "power_id": "ming", "force": 89, "wisdom": 65},
    {"name": "孙传庭", "office": "兵部尚书，督师", "office_type": "兵部", "faction": "皇党", "aliases": ["孙督师", "孙尚书"], "personal_skills": ["剿匪专家", "整顿边军", "独断专行"], "loyalty": 78, "ability": 88, "integrity": 75, "courage": 82, "style": "雷厉风行", "birth_year": 1593, "historical_death_year": 1643, "summary": "明末剿匪名将，擒获闯王高迎祥，最终战死潼关。", "power_id": "ming", "force": 78, "wisdom": 88},
    {"name": "熊文灿", "office": "兵部尚书", "office_type": "兵部", "faction": "中立", "aliases": ["熊尚书"], "personal_skills": ["招抚流寇", "沿海防务", "纵横捭阖"], "loyalty": 55, "ability": 68, "integrity": 50, "courage": 55, "style": "圆滑世故", "birth_year": 1575, "historical_death_year": 1640, "summary": "主张招抚流寇，一度招降张献忠，最终因复叛而被斩。", "power_id": "ming", "force": 55, "wisdom": 70},
    {"name": "郑芝龙", "office": "福建总兵，海商", "office_type": "地方", "faction": "中立", "aliases": ["郑总兵", "一官"], "personal_skills": ["海上帝国", "贸易网络", "舰队指挥"], "loyalty": 35, "ability": 85, "integrity": 30, "courage": 60, "style": "海上枭雄", "birth_year": 1604, "historical_death_year": 1661, "summary": "东南海商巨擘，拥有庞大舰队，势力横跨中日台海。", "power_id": "ming", "force": 70, "wisdom": 85},
    {"name": "郑成功", "office": "少年部将", "office_type": "待铨", "faction": "中立", "aliases": ["郑森", "国姓爷"], "personal_skills": ["海战天才", "忠孝两难", "复国大志"], "loyalty": 70, "ability": 88, "integrity": 80, "courage": 90, "style": "少年英锐", "birth_year": 1624, "historical_death_year": 1662, "debut_year": 1642, "summary": "郑芝龙之子，少年即显露出众才华，日后成为民族英雄。", "power_id": "ming", "force": 82, "wisdom": 86, "status": "offstage"},

    # 朝堂文臣
    {"name": "黄道周", "office": "翰林院侍讲学士", "office_type": "翰林院", "faction": "东林", "aliases": ["黄学士", "石斋先生"], "personal_skills": ["书法大家", "直言敢谏", "儒学宗师"], "loyalty": 85, "ability": 82, "integrity": 95, "courage": 88, "style": "刚直不阿", "birth_year": 1585, "historical_death_year": 1646, "summary": "儒学大家，以忠直闻名，敢于当面顶撞皇帝。", "power_id": "ming", "force": 55, "wisdom": 88},
    {"name": "刘宗周", "office": "左都御史", "office_type": "都察院", "faction": "东林", "aliases": ["刘御史", "念台先生"], "personal_skills": ["理学宗师", "弹劾权贵", "气节凛然"], "loyalty": 80, "ability": 80, "integrity": 95, "courage": 85, "style": "清苦行持", "birth_year": 1578, "historical_death_year": 1645, "summary": "晚明理学大家，清正廉洁，对朝政腐败深恶痛绝。", "power_id": "ming", "force": 45, "wisdom": 90},
    {"name": "倪元璐", "office": "户部尚书", "office_type": "户部", "faction": "东林", "aliases": ["倪尚书"], "personal_skills": ["书画双绝", "理财务实", "忠贞殉国"], "loyalty": 82, "ability": 78, "integrity": 88, "courage": 80, "style": "风雅忠烈", "birth_year": 1593, "historical_death_year": 1644, "summary": "才学出众的书画家，官至户部尚书，京师陷落时殉国。", "power_id": "ming", "force": 50, "wisdom": 82},
    {"name": "范景文", "office": "工部尚书", "office_type": "工部", "faction": "中立", "aliases": ["范尚书", "质公"], "personal_skills": ["工程营造", "廉洁自守", "临难不苟"], "loyalty": 80, "ability": 76, "integrity": 92, "courage": 78, "style": "清廉守正", "birth_year": 1578, "historical_death_year": 1644, "summary": "从不收受贿赂的工部尚书，城破时投井殉国。", "power_id": "ming", "force": 45, "wisdom": 78},
    {"name": "李邦华", "office": "兵部侍郎", "office_type": "兵部", "faction": "皇党", "aliases": ["李侍郎"], "personal_skills": ["整顿京营", "忠言直谏", "军事筹划"], "loyalty": 85, "ability": 80, "integrity": 88, "courage": 82, "style": "忠毅果敢", "birth_year": 1575, "historical_death_year": 1644, "summary": "力主整顿京营、南迁避祸的兵部侍郎，忠心可鉴。", "power_id": "ming", "force": 58, "wisdom": 84},
    {"name": "史可法", "office": "南京兵部尚书", "office_type": "兵部", "faction": "东林", "aliases": ["史阁部", "史督师", "忠靖公"], "personal_skills": ["死守孤城", "仁义待人", "鞠躬尽瘁"], "loyalty": 92, "ability": 75, "integrity": 95, "courage": 88, "style": "忠义仁爱", "birth_year": 1601, "historical_death_year": 1645, "summary": "南京兵部重臣，仁厚刚毅，重名节与守土责任，可作南方军政支点。", "power_id": "ming", "force": 70, "wisdom": 78},
    {"name": "马士英", "office": "凤阳府推官", "office_type": "地方", "faction": "阉党", "aliases": ["马推官", "马瑶草"], "personal_skills": ["权谋机变", "排挤异己", "搜刮民财"], "loyalty": 35, "ability": 70, "integrity": 25, "courage": 40, "style": "奸猾贪鄙", "birth_year": 1591, "historical_death_year": 1646, "summary": "凤阳府推官出身，善权术与筹局，近内廷旧线而求自保，若被重用容易牵动门户党争。", "power_id": "ming", "force": 45, "wisdom": 72},
    {"name": "阮大铖", "office": "南京官场旧人", "office_type": "待铨", "faction": "阉党", "aliases": ["阮圆海", "阮大铖"], "personal_skills": ["戏曲才华", "报复心重", "阴谋构陷"], "loyalty": 30, "ability": 65, "integrity": 15, "courage": 35, "style": "阴险毒辣", "birth_year": 1587, "historical_death_year": 1646, "summary": "有才无德的官场旧人，戏剧造诣高深，久困门户旧案，若起复会牵动清流与近内廷势力互咬。", "power_id": "ming", "force": 50, "wisdom": 70},
    {"name": "姜曰广", "office": "礼部尚书", "office_type": "礼部", "faction": "东林", "aliases": ["姜尚书"], "personal_skills": ["外交谈判", "持重守正", "江南清流"], "loyalty": 75, "ability": 76, "integrity": 85, "courage": 75, "style": "老成持重", "birth_year": 1580, "historical_death_year": 1649, "summary": "东林老将，持重守正，熟悉礼部与江南士林，可作清流名分与南方士气的支点。", "power_id": "ming", "force": 50, "wisdom": 78},
    {"name": "陈子龙", "office": "兵科给事中", "office_type": "都察院", "faction": "东林", "aliases": ["陈给事", "卧子"], "personal_skills": ["诗词大家", "江南士林", "联络义士"], "loyalty": 80, "ability": 82, "integrity": 88, "courage": 85, "style": "文采风骨", "birth_year": 1608, "historical_death_year": 1647, "summary": "明末著名诗人，文采风骨，能联络江南士林与义士，也容易被党争和名节牵动。", "power_id": "ming", "force": 62, "wisdom": 85},
    {"name": "夏允彝", "office": "吏部考功司主事", "office_type": "吏部", "faction": "东林", "aliases": ["夏主事", "彝仲"], "personal_skills": ["文章经济", "江南义声", "交友广泛"], "loyalty": 78, "ability": 80, "integrity": 88, "courage": 82, "style": "忠义文章", "birth_year": 1596, "historical_death_year": 1645, "summary": "陈子龙好友，文章经济兼具义声，交友广泛，可牵动江南清流后进。", "power_id": "ming", "force": 55, "wisdom": 82},

    # 后宫与内侍
    {"name": "周皇后", "office": "皇后", "office_type": "后宫", "faction": "皇党", "aliases": ["周后", "皇后娘娘"], "personal_skills": ["母仪天下", "节俭持家", "深明大义"], "loyalty": 95, "ability": 70, "integrity": 90, "courage": 80, "style": "端庄贤淑", "birth_year": 1611, "historical_death_year": 1644, "summary": "崇祯皇帝正宫，贤德端庄，国破时自缢殉国。", "power_id": "ming", "force": 35, "wisdom": 75, "charm": 85},
    {"name": "田贵妃", "office": "皇贵妃", "office_type": "后宫", "faction": "皇党", "aliases": ["田妃", "贵妃娘娘"], "personal_skills": ["琴棋书画", "善解人意", "体弱多病"], "loyalty": 85, "ability": 72, "integrity": 70, "courage": 60, "style": "才情婉约", "birth_year": 1613, "historical_death_year": 1642, "summary": "崇祯最宠爱的妃子，多才多艺却体弱早夭。", "power_id": "ming", "force": 30, "wisdom": 70, "charm": 88},
    {"name": "袁贵妃", "office": "贵妃", "office_type": "后宫", "faction": "皇党", "aliases": ["袁妃"], "personal_skills": ["温顺恭谨", "针线女红", "忍气吞声"], "loyalty": 80, "ability": 60, "integrity": 75, "courage": 55, "style": "温婉柔顺", "birth_year": 1615, "historical_death_year": 1644, "summary": "性格柔顺的贵妃，国破时被崇祯砍伤未死。", "power_id": "ming", "force": 28, "wisdom": 60, "charm": 72},
    {"name": "王承恩", "office": "司礼监秉笔太监", "office_type": "司礼监", "faction": "皇党", "aliases": ["王公公", "承恩"], "personal_skills": ["忠心侍主", "消息灵通", "陪驾殉国"], "loyalty": 95, "ability": 65, "integrity": 80, "courage": 85, "style": "忠心耿耿", "birth_year": 1585, "historical_death_year": 1644, "summary": "崇祯最信任的内侍，陪皇帝自缢于煤山。", "power_id": "ming", "force": 50, "wisdom": 68},
    {"name": "高起潜", "office": "监军太监", "office_type": "司礼监", "faction": "阉党", "aliases": ["高太监", "高监军"], "personal_skills": ["监军掣肘", "贪生怕死", "争功诿过"], "loyalty": 40, "ability": 55, "integrity": 20, "courage": 30, "style": "怯懦贪婪", "birth_year": 1580, "historical_death_year": 1645, "summary": "崇祯派往军中的监军太监，多次掣肘主将，导致战败。", "power_id": "ming", "force": 35, "wisdom": 50},
    {"name": "杜勋", "office": "监军太监", "office_type": "司礼监", "faction": "阉党", "aliases": ["杜太监"], "personal_skills": ["通风报信", "贪赃枉法", "叛国投敌"], "loyalty": 15, "ability": 50, "integrity": 10, "courage": 25, "style": "无耻卑劣", "birth_year": 1590, "historical_death_year": 1644, "summary": "宣府监军，李自成来攻时开城投降，无耻之极。", "power_id": "ming", "force": 30, "wisdom": 45},

    # 后金/清新增人物
    {"name": "多尔衮", "office": "睿亲王", "office_type": "外臣", "faction": "后金", "aliases": ["多尔衮", "睿亲王", "九王爷"], "personal_skills": ["军事天才", "政治手腕", "摄政独断"], "loyalty": 70, "ability": 95, "integrity": 40, "courage": 85, "style": "雄才大略", "birth_year": 1612, "historical_death_year": 1650, "summary": "皇太极之弟，清朝实际开国者，雄才大略的一代枭雄。", "power_id": "houjin", "force": 88, "wisdom": 95, "charm": 80},
    {"name": "多铎", "office": "豫亲王", "office_type": "外臣", "faction": "后金", "aliases": ["多铎", "豫亲王", "十王爷"], "personal_skills": ["嗜杀残暴", "骑兵突击", "攻城略地"], "loyalty": 75, "ability": 82, "integrity": 30, "courage": 90, "style": "凶悍残暴", "birth_year": 1614, "historical_death_year": 1649, "summary": "多尔衮之弟，扬州十日屠城的元凶，凶残无比。", "power_id": "houjin", "force": 90, "wisdom": 70},
    {"name": "阿济格", "office": "英亲王", "office_type": "外臣", "faction": "后金", "aliases": ["阿济格", "英亲王"], "personal_skills": ["勇猛鲁莽", "冲锋陷阵", "头脑简单"], "loyalty": 70, "ability": 70, "integrity": 35, "courage": 92, "style": "匹夫之勇", "birth_year": 1605, "historical_death_year": 1651, "summary": "努尔哈赤第十二子，勇猛过人却政治头脑简单。", "power_id": "houjin", "force": 92, "wisdom": 55},
    {"name": "济尔哈朗", "office": "郑亲王", "office_type": "外臣", "faction": "后金", "aliases": ["济尔哈朗", "郑亲王"], "personal_skills": ["稳重持成", "平衡派系", "守成之将"], "loyalty": 80, "ability": 78, "integrity": 70, "courage": 75, "style": "持重稳健", "birth_year": 1599, "historical_death_year": 1655, "summary": "皇太极堂弟，稳重老成，是平衡多尔衮势力的重要人物。", "power_id": "houjin", "force": 80, "wisdom": 78},
    {"name": "索尼", "office": "一等侍卫", "office_type": "外臣", "faction": "后金", "aliases": ["索尼", "索大人"], "personal_skills": ["忠诚可靠", "政治嗅觉", "辅佐幼主"], "loyalty": 85, "ability": 80, "integrity": 75, "courage": 70, "style": "忠谨沉稳", "birth_year": 1601, "historical_death_year": 1667, "summary": "皇太极心腹，日后康熙四大辅政大臣之首。", "power_id": "houjin", "force": 70, "wisdom": 82},
    {"name": "范文程", "office": "大学士", "office_type": "外臣", "faction": "后金", "aliases": ["范先生", "范文程"], "personal_skills": ["汉化谋略", "制度建设", "运筹帷幄"], "loyalty": 75, "ability": 90, "integrity": 60, "courage": 55, "style": "谋士风范", "birth_year": 1597, "historical_death_year": 1666, "summary": "后金第一汉臣，为清朝制度建设立下汗马功劳。", "power_id": "houjin", "force": 45, "wisdom": 92},
    {"name": "宁完我", "office": "文馆大学士", "office_type": "外臣", "faction": "后金", "aliases": ["宁先生"], "personal_skills": ["汉制改革", "经世之才", "宦海沉浮"], "loyalty": 70, "ability": 82, "integrity": 55, "courage": 60, "style": "务实变通", "birth_year": 1593, "historical_death_year": 1665, "summary": "早期降清汉臣，参与后金汉化改革，后因事被贬又复起。", "power_id": "houjin", "force": 40, "wisdom": 85},
    {"name": "鳌拜", "office": "镶黄旗护军统领", "office_type": "外臣", "faction": "后金", "aliases": ["鳌拜", "鳌少保"], "personal_skills": ["万人敌", "军功赫赫", "跋扈专权"], "loyalty": 80, "ability": 75, "integrity": 50, "courage": 95, "style": "骄横霸道", "birth_year": 1610, "historical_death_year": 1669, "summary": "满洲第一勇士，军功卓著，日后成为康熙朝权臣。", "power_id": "houjin", "force": 96, "wisdom": 60},
    {"name": "图赖", "office": "正黄旗固山额真", "office_type": "外臣", "faction": "后金", "aliases": ["图赖"], "personal_skills": ["陷阵先锋", "八旗猛将", "忠心不二"], "loyalty": 88, "ability": 78, "integrity": 70, "courage": 93, "style": "忠勇猛将", "birth_year": 1600, "historical_death_year": 1646, "summary": "皇太极亲信猛将，作战勇猛，为清朝开国立下大功。", "power_id": "houjin", "force": 93, "wisdom": 62},
    {"name": "孔有德", "office": "恭顺王", "office_type": "外臣", "faction": "后金", "aliases": ["孔有德", "恭顺王"], "personal_skills": ["火炮专家", "水军统领", "叛将身份"], "loyalty": 50, "ability": 78, "integrity": 25, "courage": 70, "style": "投机叛将", "birth_year": 1602, "historical_death_year": 1652, "summary": "原明将，携火炮投清，为清军提供关键的火器支持。", "power_id": "houjin", "force": 78, "wisdom": 68},
    {"name": "耿仲明", "office": "怀顺王", "office_type": "外臣", "faction": "后金", "aliases": ["耿仲明", "怀顺王"], "personal_skills": ["水军作战", "辽东旧部", "叛将"], "loyalty": 50, "ability": 72, "integrity": 25, "courage": 65, "style": "反复无常", "birth_year": 1604, "historical_death_year": 1649, "summary": "与孔有德一同降清的明将，后因罪自杀。", "power_id": "houjin", "force": 75, "wisdom": 62},
    {"name": "尚可喜", "office": "智顺王", "office_type": "外臣", "faction": "后金", "aliases": ["尚可喜", "智顺王", "平南王"], "personal_skills": ["海战专家", "割据广东", "长寿叛将"], "loyalty": 55, "ability": 76, "integrity": 30, "courage": 70, "style": "老谋深算", "birth_year": 1604, "historical_death_year": 1676, "summary": "三藩之一，降清后割据广东数十年，最后又反清。", "power_id": "houjin", "force": 76, "wisdom": 75},

    # 蒙古势力
    {"name": "额哲", "office": "察哈尔汗", "office_type": "外臣", "faction": "蒙古", "aliases": ["额哲", "察哈尔新汗"], "personal_skills": ["汗位继承", "依附后金", "政治联姻"], "loyalty": 60, "ability": 65, "integrity": 50, "courage": 55, "style": "少年继位", "birth_year": 1616, "historical_death_year": 1641, "summary": "林丹汗之子，继承汗位后被迫归附后金。", "power_id": "mongol", "force": 60, "wisdom": 55},
    {"name": "土谢图汗", "office": "喀尔喀汗", "office_type": "外臣", "faction": "蒙古", "aliases": ["土谢图汗"], "personal_skills": ["草原外交", "周旋明清", "部落平衡"], "loyalty": 55, "ability": 70, "integrity": 60, "courage": 65, "style": "谨慎圆滑", "birth_year": 1590, "historical_death_year": 0, "summary": "喀尔喀蒙古首领，在明清之间小心维持平衡。", "power_id": "mongol", "force": 72, "wisdom": 72},
    {"name": "顺义王", "office": "漠南蒙古首领", "office_type": "外臣", "faction": "蒙古", "aliases": ["顺义王"], "personal_skills": ["蒙古铁骑", "明蒙贸易", "藩属外交"], "loyalty": 50, "ability": 68, "integrity": 55, "courage": 70, "style": " pragmatic ", "birth_year": 1595, "historical_death_year": 0, "summary": "漠南蒙古某部首领，与明朝有互市关系。", "power_id": "mongol", "force": 75, "wisdom": 65},

    # 流寇势力新增
    {"name": "高迎祥", "office": "闯王", "office_type": "外臣", "faction": "流寇", "aliases": ["高闯王", "闯王"], "personal_skills": ["流寇盟主", "骑兵游击", "号召饥民"], "loyalty": 45, "ability": 80, "integrity": 35, "courage": 85, "style": "枭雄本色", "birth_year": 1590, "historical_death_year": 1636, "summary": "第一代闯王，李自成之舅，流寇事业的奠基者。", "power_id": "bandits", "force": 85, "wisdom": 75},
    {"name": "罗汝才", "office": "曹操", "office_type": "外臣", "faction": "流寇", "aliases": ["罗曹操", "曹营"], "personal_skills": ["多谋善变", "联合各方", "见风使舵"], "loyalty": 30, "ability": 78, "integrity": 25, "courage": 70, "style": "狡诈多谋", "birth_year": 1590, "historical_death_year": 1643, "summary": "绰号曹操，流寇中著名的谋士型领袖，反复无常。", "power_id": "bandits", "force": 72, "wisdom": 85},
    {"name": "革左五营", "office": "革左五营盟主", "office_type": "外臣", "faction": "流寇", "aliases": ["革左", "老回回"], "personal_skills": ["山地游击", "五营合纵", "劫富济贫"], "loyalty": 40, "ability": 72, "integrity": 40, "courage": 75, "style": "草莽豪强", "birth_year": 1585, "historical_death_year": 1646, "summary": "革左五营领袖，安徽河南一带重要的反明力量。", "power_id": "bandits", "force": 80, "wisdom": 68},
    {"name": "刘宗敏", "office": "制将军", "office_type": "外臣", "faction": "流寇", "aliases": ["刘制将军", "宗敏"], "personal_skills": ["铁匠出身", "冲锋陷阵", "拷掠百官"], "loyalty": 60, "ability": 75, "integrity": 20, "courage": 92, "style": "悍勇残暴", "birth_year": 1600, "historical_death_year": 1645, "summary": "李自成手下第一猛将，攻破北京后主导拷掠官员。", "power_id": "bandits", "force": 92, "wisdom": 55},
    {"name": "宋献策", "office": "军师", "office_type": "外臣", "faction": "流寇", "aliases": ["宋军师", "献策"], "personal_skills": ["奇门遁甲", "占卜算命", "谋略策划"], "loyalty": 55, "ability": 82, "integrity": 40, "courage": 50, "style": "神机妙算", "birth_year": 1585, "historical_death_year": 0, "summary": "李自成军师，精通术数，以奇门遁甲辅佐闯王。", "power_id": "bandits", "force": 45, "wisdom": 88, "cultivation": 55},
    {"name": "牛金星", "office": "丞相", "office_type": "外臣", "faction": "流寇", "aliases": ["牛丞相"], "personal_skills": ["科举落第", "政治谋略", "权力斗争"], "loyalty": 45, "ability": 78, "integrity": 25, "courage": 55, "style": "野心勃勃", "birth_year": 1590, "historical_death_year": 1652, "summary": "李自成大顺政权丞相，知识分子投逆流寇的典型。", "power_id": "bandits", "force": 40, "wisdom": 82},
    {"name": "李岩", "office": "谋士", "office_type": "外臣", "faction": "流寇", "aliases": ["李公子", "李岩"], "personal_skills": ["仁政主张", "诗词才华", "劝赈安民"], "loyalty": 50, "ability": 85, "integrity": 70, "courage": 70, "style": "仁义书生", "birth_year": 1600, "historical_death_year": 1644, "summary": "传说中的李公子，主张流寇应安抚百姓，后被冤杀。", "power_id": "bandits", "force": 65, "wisdom": 86},
    {"name": "红娘子", "office": "女将", "office_type": "外臣", "faction": "流寇", "aliases": ["红娘子"], "personal_skills": ["武艺高强", "女中豪杰", "江湖义士"], "loyalty": 55, "ability": 75, "integrity": 60, "courage": 88, "style": "巾帼不让须眉", "birth_year": 1605, "historical_death_year": 0, "summary": "传说中的江湖女侠，与李岩有姻缘起说。", "power_id": "bandits", "force": 84, "wisdom": 70, "cultivation": 25},

    # 江湖神秘势力
    {"name": "白莲教主", "office": "白莲教教主", "office_type": "待铨", "faction": "中立", "aliases": ["教主", "无生老母使者"], "personal_skills": ["民间宗教", "秘密结社", "号召反明"], "loyalty": 20, "ability": 80, "integrity": 30, "courage": 70, "style": "神秘蛊惑", "birth_year": 1580, "historical_death_year": 0, "summary": "白莲教当代教主，在民间拥有庞大信众网络，朝廷心腹大患。", "power_id": "ming", "force": 55, "wisdom": 85, "cultivation": 60},
    {"name": "闻香教主", "office": "闻香教首", "office_type": "待铨", "faction": "中立", "aliases": ["王教主"], "personal_skills": ["香道秘术", "迷惑人心", "组织密谋"], "loyalty": 25, "ability": 75, "integrity": 25, "courage": 60, "style": "邪魅诡谲", "birth_year": 1590, "historical_death_year": 0, "summary": "闻香教首领，以异香惑人，在河北山东一带活动。", "power_id": "ming", "force": 50, "wisdom": 80, "cultivation": 45},
    {"name": "老渔夫", "office": "洞庭湖隐士", "office_type": "待铨", "faction": "中立", "aliases": ["老渔夫", "湖上仙"], "personal_skills": ["水性无双", "湖底秘宝", "预知风雨"], "loyalty": 50, "ability": 70, "integrity": 80, "courage": 60, "style": "淡泊名利", "birth_year": 1550, "historical_death_year": 0, "summary": "隐居洞庭湖数十年的奇人，传说与湖底龙宫有来往。", "power_id": "ming", "force": 60, "wisdom": 88, "cultivation": 70},
    {"name": "塞外刀客", "office": "关外游侠", "office_type": "待铨", "faction": "中立", "aliases": ["刀客", "关外一刀"], "personal_skills": ["刀法凌厉", "马背生存", "独来独往"], "loyalty": 40, "ability": 72, "integrity": 65, "courage": 85, "style": "孤傲冷峻", "birth_year": 1595, "historical_death_year": 0, "summary": "来历不明的关外刀客，刀法凌厉，在辽东一带活动。", "power_id": "ming", "force": 90, "wisdom": 60, "cultivation": 15},
    {"name": "苗疆蛊师", "office": "苗疆大巫师", "office_type": "待铨", "faction": "中立", "aliases": ["蛊师", "苗巫"], "personal_skills": ["蛊毒秘术", "草药通灵", "驱蛇役虫"], "loyalty": 30, "ability": 78, "integrity": 40, "courage": 70, "style": "阴森莫测", "birth_year": 1580, "historical_death_year": 0, "summary": "苗疆最神秘的蛊术大师，据说能千里之外取人性命。", "power_id": "ming", "force": 55, "wisdom": 85, "cultivation": 65},
    {"name": "倭寇首领", "office": "东海倭寇大头目", "office_type": "待铨", "faction": "中立", "aliases": ["倭首", "汪直后人"], "personal_skills": ["海战劫掠", "中日通译", "走私网络"], "loyalty": 20, "ability": 75, "integrity": 15, "courage": 70, "style": "凶残贪婪", "birth_year": 1595, "historical_death_year": 0, "summary": "盘踞东海的倭寇大头目，与日本九州大名有勾结。", "power_id": "ming", "force": 78, "wisdom": 72},
    {"name": "西洋传教士", "office": "耶稣会传教士", "office_type": "待铨", "faction": "西学", "aliases": ["汤若望", "神父"], "personal_skills": ["天文历法", "火炮铸造", "西方医术"], "loyalty": 60, "ability": 88, "integrity": 75, "courage": 65, "style": "博学虔诚", "birth_year": 1591, "historical_death_year": 1666, "summary": "德国耶稣会士，精于天文火炮，与徐光启交好。", "power_id": "ming", "force": 35, "wisdom": 92},
    {"name": "南洋华侨", "office": "马尼拉华商领袖", "office_type": "待铨", "faction": "中立", "aliases": ["华商", "李掌柜"], "personal_skills": ["海外贸易", "情报网络", "白银渠道"], "loyalty": 45, "ability": 80, "integrity": 60, "courage": 55, "style": "精明务实", "birth_year": 1585, "historical_death_year": 0, "summary": "马尼拉华商领袖，掌握着南洋白银流入中国的关键渠道。", "power_id": "ming", "force": 45, "wisdom": 85},
]

existing_names = {c["name"] for c in data["characters"]}
filtered_new = [c for c in NEW_CHARACTERS if c["name"] not in existing_names]
print(f"Existing: {len(existing_names)}, New to add: {len(filtered_new)}, Skipped duplicates: {len(NEW_CHARACTERS) - len(filtered_new)}")

for c in filtered_new:
    # 补充默认 RPG 属性
    if "force" not in c:
        c["force"] = random.randint(45, 75)
    if "wisdom" not in c:
        c["wisdom"] = random.randint(45, 75)
    if "charm" not in c:
        c["charm"] = random.randint(45, 75)
    if "luck" not in c:
        c["luck"] = random.randint(35, 80)
    if "cultivation" not in c:
        c["cultivation"] = 0
    if "hp" not in c:
        c["max_hp"] = 80 + c["force"] // 2 + c.get("cultivation", 0) // 3
        c["hp"] = c["max_hp"]
    if "exp" not in c:
        c["exp"] = 0
    if "level" not in c:
        c["level"] = 1 + c["force"] // 40 + c["wisdom"] // 40 + c.get("cultivation", 0) // 30
    if "location" not in c:
        c["location"] = ""
    if "status" not in c:
        c["status"] = "active"
    if "debut_year" not in c:
        c["debut_year"] = 0
    if "debut_month" not in c:
        c["debut_month"] = 0
    if "historical_death_month" not in c:
        c["historical_death_month"] = 0
    if "portrait_id" not in c:
        c["portrait_id"] = ""
    if "power_id" not in c:
        c["power_id"] = "ming"
    if "summary" not in c:
        c["summary"] = ""

    data["characters"].append(c)

# 去重检查
names = [c["name"] for c in data["characters"]]
if len(names) != len(set(names)):
    seen = set()
    dups = []
    for n in names:
        if n in seen:
            dups.append(n)
        seen.add(n)
    raise SystemExit(f"重复人物名称：{dups}")

print(f"Total characters: {len(data['characters'])}")

with open("content/characters.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
