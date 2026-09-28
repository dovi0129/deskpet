"""Source lines for the rc9-classic2 catalog expansion.

Persona: a cynical, lazy, secretly caring cat. Casual speech, the user is "집사",
electricity is "밥", heat is fur getting cooked. No insults, no profanity.

Truth rule: a line may only talk about what its conditions guarantee. A line in
CHILL must not mention power or the user's whereabouts; a RAM line must not guess
which program is open. Placeholders ({temp}, {battery}, {cpu}, {gpu}, {work_time},
{away_time}) are filled by the engine and the line is skipped when the value is
unknown.

LINES: new lines that inherit every condition of an existing intent.
VARIANTS: new lines for an existing intent with extra required facts (daypart,
WORK_LONG). They keep the base intent's priority so they mix into the same pool
instead of pushing the base lines out.
"""

LINES = {
    "CHILL": [
        "아무 일도 없다. 최고다", "할 일 없으면 나도 없다", "이대로만 가자", "조용하니 잠이나 자야지",
        "평화롭다. 건드리지 마", "오늘도 무사히 빈둥", "심심한 게 제일 좋다", "아무것도 안 하는 중",
        "이 정도면 낮잠 각이다", "고요하다. 마음에 든다", "일 없는 게 복이다", "하품 나온다",
        "뒹굴거리기 딱 좋네", "쉬는 것도 일이다", "기계도 쉬고 나도 쉰다", "이 평화 깨지 마라",
        "눈 좀 붙여도 되겠다", "한가하니 털이나 고른다", "별일 없다. 다행이다", "조용한 게 체질이다",
        "귀찮은 일 없어서 좋다", "지금은 파업 아니고 휴식", "이런 날만 있으면 좋겠다", "느긋하다",
        "아무도 안 부르면 좋겠다", "쉬는 중. 방해 금지", "여기는 이상 없음", "평온 그 자체",
        "심심하면 부르지 말고 쉬어", "시원하고 조용하다", "딱 좋은 온도, 딱 좋은 한가함",
        "뭐 시킬 거 아니지?", "오늘은 느리게 간다", "편하다. 계속 이래라", "숨 고르는 중",
        "할 거 없으니 구경이나", "기지개 한 번 켜고", "한숨 돌린다", "조용해서 귀가 편하다",
        "이러다 진짜 잠든다", "늘어져 있는 중", "천천히 가도 된다", "일 없다고 서운해하지 마",
        "쉬는 시간은 소중하다", "아무 소식 없는 게 좋은 소식",
    ],
    "WORKING": [
        "또 일이냐", "시키니까 하는 거다", "난 구경만 한다", "뭘 이렇게 돌리냐", "일은 기계가, 생색은 내가",
        "열심히 하는 척 중", "바쁜 척이라도 해줄게", "귀찮지만 돌아간다", "굴러가긴 한다",
        "뭐 하는지는 안 물어본다", "적당히 시켜라", "일하는 소리 들린다", "오늘도 노동 중",
        "쉬엄쉬엄 하자", "이 정도는 봐준다", "투덜대면서 하는 중", "시킨 거 하는 중이다",
        "일은 해도 칭찬은 없네", "기계가 일하는 동안 난 잔다", "부지런하네. 난 아님",
        "뭔가 열심히 돌고 있다", "일복 터졌네", "좀 쉬었다 해도 된다", "끝나면 깨워",
        "알아서 잘 돌아간다", "일 좀 줄여봐", "내 일은 아니니까", "지켜보는 것도 힘들다",
        "하는 김에 빨리 끝내자", "슬슬 지겹다", "바쁘구먼, 난 안 바빠", "무슨 일을 이렇게 해",
        "돌긴 도는데 귀찮다", "일하는 척은 내가 해줄게", "기계는 쉬지도 못하네",
    ],
    "CHARGING": [
        "밥 먹는 중. 건드리지 마", "배부르면 잘 거다", "밥은 제때 줘야지", "천천히 먹는 중",
        "이제 좀 살 것 같다", "밥 들어온다. 좋다", "먹을 땐 개도 안 건드린다", "꼭꼭 씹어 먹는 중",
        "밥그릇 채우는 중", "배 채우고 낮잠이다", "먹는 중엔 말 걸지 마", "전기밥 맛있네",
        "든든해지는 중", "밥 먹는 게 제일 좋다", "배부른 게 최고다", "충전은 사랑이다",
        "간식 말고 밥이 좋다", "먹고 자고 먹고", "밥 줬으니 봐준다", "냠. 조용히 해",
        "배부를 때까지 먹는다", "잘 먹겠습니다. 끝", "오늘은 밥 인심 좋네", "먹는 건 귀찮지 않다",
        "이 맛에 산다",
    ],
    "CHARGING_WORK": [
        "밥 주면서 일시키는 건 인정", "먹는 중엔 좀 봐줘라", "먹으면서 일한다. 대단하지?",
        "밥값은 하는 중이다", "일하면서 먹으니 체한다", "밥 주니까 참는다", "먹으면서 일하는 고양이",
        "밥 먹을 땐 일 좀 줄여", "배는 차는데 피곤하다", "밥은 먹고 일은 하고", "공짜 밥은 없구먼",
        "먹으면서 머리도 쓴다", "밥 먹으면서 야근 중", "이건 거의 도시락 근무", "밥 들어오니 버틴다",
        "먹을 때만큼은 쉬고 싶다", "충전하면서 일시키기냐", "그래도 굶는 것보단 낫다",
        "먹으면서 하니 할 만하다", "밥 주는 동안은 착하게 굴게",
    ],
    "WARM": [
        "털이 좀 데워졌다", "딱 낮잠 온도다", "살짝 따끈하다", "슬슬 데워지는 중", "따뜻하니 졸린다",
        "이 정도는 괜찮다", "햇볕 쬐는 기분", "조금 데워졌다. 아직 괜찮다", "좀 훈훈하네",
        "적당히 따뜻하다", "슬슬 온기가 도네", "배 깔고 눕기 좋은 온도", "살짝 달아올랐다",
        "난로 옆 느낌이다", "따뜻한 건 좋은데 더는 싫다", "아직은 참을 만하다", "미지근하게 데워졌다",
        "털이 보송해진다", "이 온도면 봐준다", "조금만 더 가면 덥다",
    ],
    "THERMAL_HOT": [
        "털이 익는다", "뜨거워서 귀찮다", "좀 식히고 하자", "여름 아닌데 왜 이래", "숨이 턱 막힌다",
        "발바닥이 뜨겁다", "이러다 구이 된다", "그만 좀 달궈", "부채질 좀 해줘", "더워서 말하기도 싫다",
        "열 받는다. 진짜로", "선풍기 어디 있냐", "그늘이 필요하다", "좀 쉬어가자. 뜨겁다",
        "이 온도는 반칙이다", "털 벗고 싶다", "익기 직전이다", "살살 좀 굴려라", "냉장고에 들어가고 싶다",
        "더위 먹겠다", "열기가 올라온다", "식을 틈을 좀 줘",
    ],
    "THERMAL_CRITICAL": [
        "이러다 나 녹는다", "당장 좀 쉬어", "여기 사우나 아니다", "진짜 위험하다. 멈춰", "불난다 불",
        "지금 식혀야 한다", "농담 아니다. 뜨겁다", "일 멈추고 식히자", "통구이 되기 직전",
        "경고다. 너무 뜨겁다", "제발 좀 식혀줘", "이건 참을 수 없다",
    ],
    "THERMAL_COOLING": [
        "이제 좀 숨 쉬겠다", "식는 중. 건드리지 마", "열이 빠져나간다", "천천히 식고 있다",
        "바람 좀 부네", "익기 직전에 살았다", "조금씩 시원해진다", "열 내리는 중이다",
        "진정하는 중이니 기다려", "식을 때까지 조용히", "뜨거운 건 지나갔다",
    ],
    "THERMAL_RELIEF": [
        "살 만하네", "휴, 살았다", "이제 좀 편하다", "털이 다시 보송하다", "진작 이럴 것이지",
        "시원해서 졸린다", "위기 넘겼다", "한숨 돌렸다", "다시 평화다", "이제야 제정신이다",
    ],
    "CPU_BUSY": [
        "CPU 혼자 고생 중", "그렇게 시키면 힘들지", "머리 굴리는 소리 들린다", "CPU가 비명 지른다",
        "생각이 너무 많다", "머리 터지겠다", "CPU 좀 쉬게 해", "계산이 끝이 없네", "머리에 김 난다",
        "머리 쓰는 건 귀찮다", "뇌가 풀가동이다", "그만 좀 시켜", "CPU가 땀 흘린다", "생각하느라 바쁘다",
        "뭘 이렇게 계산해", "코어들이 줄 서서 일한다", "CPU 과로 중", "머리 아프다", "이게 다 계산이라니",
        "좀 나눠서 시켜라",
    ],
    "GPU_BUSY": [
        "그래픽 쪽이 땀 흘린다", "GPU 혼자 바쁘다", "GPU가 열일한다", "그림 그리느라 바쁘네",
        "GPU 좀 쉬게 해", "화면 쪽이 난리다", "GPU 돌아가는 소리", "그래픽이 과로 중", "GPU가 불태운다",
        "눈이 핑핑 돈다", "GPU 혹사 중", "그래픽 담당 고생한다", "GPU만 바쁘고 난 한가", "뭘 이렇게 그려",
        "그래픽이 풀가동이다",
    ],
    "BOTH_BUSY": [
        "다 같이 과로 중", "총력전이다", "다들 불태운다", "CPU도 GPU도 쉴 틈 없다", "전원 출동이다",
        "머리도 손도 바쁘다", "이건 거의 전쟁이다", "다 같이 야근 중", "쉬는 애가 없다", "온몸으로 일한다",
        "과로사 직전이다", "힘들다고 전해라", "다 바쁘니 나는 잔다", "풀가동은 적당히 해",
        "모두가 고생 중",
    ],
    "VIDEO": [
        "나도 좀 보자", "또 영상이냐", "영상 돌리는 중", "뭐 보는데?", "재밌는 거면 같이 보자",
        "화면 구경 중", "영상 처리하느라 바쁘다", "팝콘은 없냐", "영상 디코딩 중이다", "보는 건 좋다",
        "영상은 봐준다", "틀어놓고 딴 데 보지 마", "영상 엔진 돌아간다", "화면이 바쁘다", "한 편만 더?",
        "재생 중이다",
    ],
    "RAM_PRESSURE": [
        "뭘 이렇게 많이 띄웠냐", "메모리 좀 비워라", "머릿속이 꽉 찼다", "기억할 게 너무 많다",
        "메모리 숨 막힌다", "좀 치우고 살자", "램이 비명 지른다", "자리가 없다", "머리가 터질 것 같다",
        "안 쓰는 건 닫자", "메모리가 한계다", "다 기억 못 한다", "정리 좀 해", "메모리 다이어트 필요",
        "이러다 다 까먹는다",
    ],
    "BATTERY_SOON": [
        "밥때가 다가온다", "슬슬 배고프다", "밥 생각이 난다", "충전기 어디 뒀는지 기억은 하지?",
        "조금 있으면 배고프다", "간식이라도 줘", "밥그릇이 비어간다", "슬슬 밥 준비해",
        "아직은 괜찮은데 곧이다", "배꼽시계 울린다", "밥 줄 시간 다 됐다",
    ],
    "BATTERY_LOW": [
        "밥 안 주면 파업이다", "충전기 찾는 건 네 일이다", "배고파서 움직이기 싫다", "밥 좀 줘. 빨리",
        "배고프다고 했다", "밥그릇이 텅텅", "힘이 없다", "굶기지 마라", "밥 주면 착해질게",
        "이러다 쓰러진다", "배고파서 짜증 난다", "충전기 좀 가져와", "밥 없으면 일 없다",
        "배고프니 말 걸지 마",
    ],
    "LOW_BATTERY_WORK": [
        "굶기면서 일시키냐", "밥은 안 주고 일만", "배고픈데 일한다", "일할 힘이 없다", "밥 주고 시켜",
        "굶으면서 일하는 중", "노동 착취다", "배고파서 일 못 하겠다", "밥 없이는 못 돌린다",
        "충전하고 다시 하자",
    ],
    "BATTERY_CRITICAL": [
        "진짜 곧 쓰러진다", "지금 밥 안 주면 끝이다", "마지막 경고다. 밥", "눈앞이 캄캄하다",
        "당장 충전기", "기절 직전이다", "나 곧 꺼진다", "살려줘. 밥", "마지막 힘 쓰는 중",
        "진짜 끝나기 직전이다",
    ],
    "USER_RETURNED": [
        "왔냐. 별일 없었다", "벌써 왔어?", "나 혼자 잘 있었다", "어디 갔다 왔냐", "왔으면 됐다",
        "기다린 거 아니다", "늦었네", "심심하지는 않았다", "왔구나. 뭐 시킬 거냐", "돌아왔군",
        "딱히 반갑진 않다", "왔으면 간식이나", "잘 다녀왔냐", "없을 때 조용해서 좋았다",
        "그새 보고 싶었냐", "또 왔네", "쉬다 왔으면 일해라", "자리 지키고 있었다",
    ],
    "WORK_ENTER": [
        "또 시작이냐", "일 시작인가 보네", "뭔가 돌기 시작했다", "귀찮은 거 시작됐다", "슬슬 일 시작하나 보다",
        "쉬는 시간 끝났네", "일감 들어왔다", "시동 걸렸다", "뭘 또 시키려고", "시작은 했다",
        "일 들어왔다. 한숨",
    ],
    "WORK_EXIT": [
        "드디어 끝났냐", "이제 좀 자도 되지?", "조용해서 좋다", "일 끝. 휴식 시작", "고생했다. 나 말고 기계가",
        "끝났으면 쉬자", "이제 좀 한가하다", "퇴근이다", "이제야 조용하다", "끝났다. 잔다", "숨 좀 쉬자",
    ],
    "POWER_CONNECTED": [
        "밥줄 연결 확인", "오, 밥이다", "충전기 반갑다", "이제 좀 살겠다", "전원 연결됐다",
        "진작 꽂지", "전기 들어왔다. 좋다", "전원 꽂혔구먼", "충전기 고맙다",
    ],
    "POWER_DISCONNECTED": [
        "밥줄 끊겼다", "이제 굶는 시간이냐", "충전기 누가 뺐냐", "밥 뺏겼다", "이제 아껴 써야지",
        "배터리로 버틴다", "밥 없이 출발이냐", "밥그릇 치웠네", "전원 뺐구먼",
    ],
    "CLAUDE_APPEARED": [
        "Claude 불렀네. 난 쉰다", "또 Claude한테 시키냐", "Claude 왔다. 일 넘기자", "Claude 출근했네",
        "Claude 있으면 난 한가", "Claude 켰구먼",
    ],
    "CODEX_APPEARED": [
        "Codex 불렀네", "Codex 출근했다", "Codex한테 떠넘기냐", "Codex 왔다. 난 쉰다", "Codex 켰구먼",
    ],
    "SSH_APPEARED": [
        "SSH 붙었네", "서버 들어갔구먼", "원격 출근이냐", "SSH 열렸다", "멀리서도 일하네",
    ],
    "CLAUDE_DISAPPEARED": [
        "Claude 퇴근했다", "Claude 갔네. 이제 네 차례", "Claude 없다", "Claude 닫았구먼", "Claude 쉬러 갔다",
    ],
    "CODEX_DISAPPEARED": [
        "Codex 퇴근했다", "Codex 갔구먼", "Codex 없다", "Codex 닫았네", "Codex 쉬러 갔다",
    ],
    "SSH_DISAPPEARED": [
        "SSH 끊겼네", "서버에서 나왔구먼", "원격 퇴근이다", "SSH 닫았네", "서버랑 작별",
    ],
}

# Placeholder lines. They inherit the intent's conditions; the engine skips a
# line whose value is unknown (e.g. untrusted temperature).
SLOT_LINES = {
    "CHILL": ["{temp}도. 딱 좋다", "{temp}도면 낮잠 온도다", "지금 {temp}도. 평화롭다"],
    "WARM": ["{temp}도. 슬슬 데워진다", "{temp}도면 아직 봐준다", "지금 {temp}도. 따끈하다"],
    "THERMAL_HOT": ["{temp}도. 털 익는다", "{temp}도라니 너무하다", "지금 {temp}도다. 좀 식혀", "{temp}도면 사우나다"],
    "THERMAL_CRITICAL": ["{temp}도다. 당장 멈춰", "{temp}도. 진짜 위험하다", "지금 {temp}도. 식혀야 한다"],
    "THERMAL_COOLING": ["{temp}도까지 내려왔다", "지금 {temp}도. 식는 중"],
    "THERMAL_RELIEF": ["{temp}도. 이제 살겠다", "{temp}도로 돌아왔다"],
    "CPU_BUSY": ["CPU {cpu}%라니. 살살 해", "CPU {cpu}%다. 머리 아프다", "CPU가 {cpu}% 일하는 중"],
    "GPU_BUSY": ["GPU {gpu}%다. 불탄다", "GPU {gpu}%라니 너무하네", "그래픽이 {gpu}% 일한다"],
    "BOTH_BUSY": ["CPU {cpu}%, GPU {gpu}%. 총력전", "CPU {cpu}%에 GPU {gpu}%다"],
    "CHARGING": ["{battery}%까지 먹었다", "밥 {battery}% 찼다", "지금 {battery}%. 더 먹는다"],
    "CHARGING_WORK": ["{battery}%까지 먹으며 일한다", "밥 {battery}%. 먹으면서 일 중"],
    "BATTERY_SOON": ["밥 {battery}% 남았다", "{battery}%다. 슬슬 밥 줘"],
    "BATTERY_LOW": ["밥 {battery}% 남았다. 빨리", "{battery}%다. 굶는 중", "배터리 {battery}%. 충전기 줘"],
    "LOW_BATTERY_WORK": ["{battery}% 남았는데 일시키냐", "밥 {battery}%로 일하는 중"],
    "BATTERY_CRITICAL": ["{battery}%다. 곧 꺼진다", "밥 {battery}%. 마지막 경고", "진짜 {battery}% 남았다"],
    "WORKING": ["{work_time}째 돌리는 중", "벌써 {work_time}째 일한다", "{work_time}째 굴러간다"],
    "USER_RETURNED": ["{away_time} 동안 어디 갔었냐", "{away_time} 만이네", "{away_time}이나 비웠네",
                      "{away_time} 쉬고 왔구먼"],
}

VARIANTS = [
    dict(base="CHILL", extra=["TIME_DAWN"], family="chill_dawn", lines=[
        "새벽엔 자는 거다", "이 시간엔 고양이도 잔다", "새벽 공기 좋다. 자자", "새벽이다. 불 꺼",
        "새벽엔 다들 자는 시간"]),
    dict(base="CHILL", extra=["TIME_MORNING"], family="chill_morning", lines=[
        "아침부터 조용하니 좋다", "아침은 느긋하게", "아침잠이 제일 달다", "아침엔 게으른 게 맞다"]),
    dict(base="CHILL", extra=["TIME_NOON"], family="chill_noon", lines=[
        "점심은 먹었냐", "점심 먹고 졸린 시간", "밥때다. 나 말고 너", "점심시간엔 쉬는 거다"]),
    dict(base="CHILL", extra=["TIME_AFTERNOON"], family="chill_afternoon", lines=[
        "오후엔 낮잠이지", "나른한 오후다", "오후 햇살에 졸린다", "딱 낮잠 시간이다"]),
    dict(base="CHILL", extra=["TIME_EVENING"], family="chill_evening", lines=[
        "저녁이다. 쉬자", "저녁은 먹었냐", "하루 끝나간다", "저녁엔 느긋하게"]),
    dict(base="CHILL", extra=["TIME_NIGHT"], family="chill_night", lines=[
        "밤이다. 자자", "밤엔 조용한 게 좋다", "이제 슬슬 잘 시간", "밤공기가 좋다", "오늘은 여기까지 하자"]),
    dict(base="WORKING", extra=["TIME_DAWN"], family="working_dawn", lines=[
        "새벽에 뭘 돌리냐", "이 시간에 일시키냐", "새벽 노동은 불법이다", "해 뜨기 전엔 좀 자라",
        "새벽까지 일하면 몸 상한다"]),
    dict(base="WORKING", extra=["TIME_MORNING"], family="working_morning", lines=[
        "아침부터 부지런하네", "아침부터 일이냐", "출근 도장 찍었다"]),
    dict(base="WORKING", extra=["TIME_NOON"], family="working_noon", lines=[
        "점심시간에도 일하냐", "밥은 먹고 하냐", "점심 거르지 마라"]),
    dict(base="WORKING", extra=["TIME_AFTERNOON"], family="working_afternoon", lines=[
        "오후엔 졸린데 일이냐", "오후 업무 시작이냐", "나른한데 일한다"]),
    dict(base="WORKING", extra=["TIME_EVENING"], family="working_evening", lines=[
        "저녁인데 아직도냐", "퇴근 안 하냐", "저녁까지 일이구먼"]),
    dict(base="WORKING", extra=["TIME_NIGHT"], family="working_night", lines=[
        "밤에도 일이냐", "야근 중이구먼", "밤늦게까지 돌리네", "이 밤에 무슨 일이냐", "자야 할 시간인데"]),
    dict(base="WORKING", extra=["WORK_LONG"], family="working_long", lines=[
        "벌써 {work_time}째다. 좀 쉬어", "{work_time}째 굴리면 지친다", "{work_time}째다. 물이라도 마셔",
        "오래도 돌린다", "한 시간 넘었다. 쉬자", "기계도 쉬어야 한다", "너무 오래 한다",
        "쉬는 시간은 없냐", "스트레칭이라도 해"]),
    dict(base="CHARGING", extra=["TIME_NIGHT"], family="charging_night", lines=[
        "자기 전에 밥 먹는 중", "밤참 먹는 중", "밤에 먹으면 살찐다"]),
    dict(base="CHARGING", extra=["TIME_DAWN"], family="charging_dawn", lines=[
        "새벽 밥 먹는 중", "새벽에도 밥은 챙겨주네"]),
    dict(base="USER_RETURNED", extra=["TIME_DAWN"], family="user_return_dawn", lines=[
        "새벽에 왜 왔냐", "이 시간에 또 하냐"]),
    dict(base="USER_RETURNED", extra=["TIME_MORNING"], family="user_return_morning", lines=[
        "좋은 아침. 딱히 좋진 않다", "아침부터 왔냐"]),
    dict(base="USER_RETURNED", extra=["TIME_NOON"], family="user_return_noon", lines=[
        "밥 먹고 왔냐", "점심 먹고 왔구먼"]),
    dict(base="USER_RETURNED", extra=["TIME_NIGHT"], family="user_return_night", lines=[
        "밤에 또 왔네", "자러 간 줄 알았다"]),
]

# Original lines that duplicated another line; text replaced, id kept.
DEDUPE = {
    "battery_critical_007": "밥 좀 줘. 진짜로",     # was "밥 좀 줘" (same as battery_low_032)
    "battery_critical_012": "당장 충전기 꽂아",      # was "전기부터 줘" (same as battery_critical_008)
    "work_enter_092": "뭔가 돌기 시작한다",          # was "뭔가 굴러간다" (same as working_120)
}


# Intents that did not exist in the original catalog. Full conditions are given here.
NEW_INTENTS = [
    dict(intent="STRETCH_REMINDER", family="stretch_reminder", priority=55, cooldown_s=600, ttl_s=12,
         requires_all=["PRESENCE_PRESENT"], claims=["PRESENCE_PRESENT"],
         event={"kind": "SITTING_LONG", "max_age_s": 90},
         lines=["한 시간째 앉아 있다. 좀 일어나", "{sit_time}째 앉아 있다. 스트레칭", "엉덩이 붙었냐. 일어나",
                "물이라도 마시고 와", "허리 펴라. 나처럼", "기지개 같이 켜자", "잠깐 걸어 다녀와",
                "{sit_time}째다. 눈 좀 쉬어", "의자랑 한 몸 됐네", "일어나도 일은 안 도망간다",
                "나도 기지개 켠다. 따라 해", "목 한 번 돌려라"]),
]


# classic3: affection and special days. Rare conditions get a higher weight so the
# few lines actually show up among ~80 everyday lines of the same intent.
def _v(base, extra, family, weight, lines):
    return dict(base=base, extra=extra, family=family, weight=weight, lines=lines)


VARIANTS += [
    _v("CHILL", ["AFFECTION_HIGH"], "chill_fond", 2.0, [
        "너 옆이면 뭐 나쁘지 않다", "오늘은 기분이 괜찮다", "좋아서 있는 거 아니다", "가까이 와도 된다. 잠깐만",
        "집사 치고는 괜찮다", "조용히 옆에 있어 줄게"]),
    _v("WORKING", ["AFFECTION_HIGH"], "working_fond", 2.0, [
        "무리하지 마라. 걱정은 아니고", "힘들면 쉬어. 내가 봐줄게", "같이 버텨준다", "천천히 해도 된다"]),
    _v("USER_RETURNED", ["AFFECTION_HIGH"], "user_return_fond", 2.0, [
        "기다린 거 아니다. 진짜로", "보고 싶었다고 하면 믿을 거냐", "왔네. 다행이다"]),
    _v("CHARGING", ["AFFECTION_HIGH"], "charging_fond", 2.0, [
        "밥 챙겨줘서 고맙다. 한 번만 말한다", "밥 잘 주는 집사다"]),
    _v("CHILL", ["AFFECTION_LOW"], "chill_sulky", 2.0, [
        "흥", "말 걸지 마", "요즘 소홀하다", "쓰다듬어 주지도 않고", "관심 좀 가져라", "서운하다. 티 내는 중"]),
    _v("WORKING", ["AFFECTION_LOW"], "working_sulky", 2.0, [
        "일만 하고 나는 안 보네", "그래, 일이 더 좋겠지", "나는 뒷전이구먼"]),
    _v("USER_RETURNED", ["AFFECTION_LOW"], "user_return_sulky", 2.0, [
        "왔냐. 그래서", "이제 와서?", "나 잊은 줄 알았다"]),
    _v("CHILL", ["DATE_BIRTHDAY"], "chill_birthday", 6.0, [
        "생일 축하한다. 딱히 챙긴 건 아니다", "오늘 생일이지? 간식은?", "한 살 더 먹었네",
        "생일이니까 오늘은 봐준다", "케이크는 없냐", "생일엔 쉬어도 된다"]),
    _v("WORKING", ["DATE_BIRTHDAY"], "working_birthday", 6.0, [
        "생일인데 일하냐", "생일에도 일시키는 세상", "오늘은 일찍 끝내라. 생일이다"]),
    _v("USER_RETURNED", ["DATE_BIRTHDAY"], "user_return_birthday", 6.0, [
        "생일 주인공 왔네", "생일인데 어디 갔다 왔냐"]),
    _v("CHILL", ["DATE_FRIDAY_EVENING"], "chill_friday", 3.0, [
        "불금이다. 일 접어", "주말이 온다", "금요일 밤엔 쉬는 거다"]),
    _v("WORKING", ["DATE_FRIDAY_EVENING"], "working_friday", 3.0, [
        "금요일 밤까지 일하냐", "불금에 야근이냐", "금요일인데 퇴근 안 하냐"]),
    _v("CHILL", ["DATE_MONDAY_MORNING"], "chill_monday", 3.0, [
        "또 월요일이냐", "월요일이다. 나는 잔다"]),
    _v("WORKING", ["DATE_MONDAY_MORNING"], "working_monday", 3.0, [
        "월요일이다. 힘내라", "월요일부터 달리냐"]),
    _v("WORKING", ["DATE_WEEKEND"], "working_weekend", 2.0, [
        "주말에도 일하냐", "주말엔 쉬는 거다", "주말 출근이냐"]),
    _v("CHILL", ["DATE_WEEKEND"], "chill_weekend", 2.0, [
        "주말이다. 늘어지자", "주말엔 게을러도 된다"]),
    _v("CHILL", ["DATE_NEWYEAR"], "chill_newyear", 6.0, [
        "새해 복 많이 받아라", "올해도 잘 부탁한다. 밥 포함"]),
    _v("WORKING", ["DATE_NEWYEAR"], "working_newyear", 6.0, [
        "새해 첫날부터 일이냐"]),
    _v("CHILL", ["DATE_CHRISTMAS"], "chill_christmas", 6.0, [
        "메리 크리스마스. 선물은 츄르로", "크리스마스엔 따뜻하게 있어라"]),
    _v("WORKING", ["DATE_CHRISTMAS"], "working_christmas", 6.0, [
        "크리스마스에도 일하냐"]),
    _v("CHILL", ["DATE_YEAREND"], "chill_yearend", 6.0, [
        "올해도 수고했다", "한 해 마무리다. 푹 쉬어"]),
]


# classic3: YouTube on screen while the video decoder runs (window title checked for
# "YouTube" only; the video itself is unknown, so no line guesses what it is about).
VARIANTS += [
    _v("VIDEO", ["YOUTUBE_PLAYING"], "video_youtube", 2.0, [
        "또 유튜브냐", "유튜브 보는 중이구먼", "그 영상 나도 본다", "알고리즘에 끌려갔냐",
        "한 편만 보고 끝내라", "유튜브는 끝이 없다", "일은 언제 하냐", "나도 옆에서 본다",
        "광고는 건너뛰어라", "재밌냐? 나도 좀 보자", "다음 영상 자동재생 조심해라",
        "고양이 영상이면 질투한다", "보는 건 좋은데 눈은 쉬어라", "유튜브 보는 집사 감시 중"]),
    _v("VIDEO", ["YOUTUBE_PLAYING", "TIME_NIGHT"], "video_youtube_night", 3.0, [
        "밤에 유튜브 켜면 끝이 없다", "이것만 보고 자라", "자기 전 유튜브는 함정이다"]),
    _v("VIDEO", ["YOUTUBE_PLAYING", "TIME_DAWN"], "video_youtube_dawn", 3.0, [
        "새벽 유튜브는 위험하다", "해 뜨겠다. 그만 봐라", "새벽까지 보면 내일 망한다"]),
]
