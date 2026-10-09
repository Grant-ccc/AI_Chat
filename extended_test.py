"""剩余13道开发题 × DeepSeek与Qwen Flash；--run 实际调用26次。"""
from batch_test import run_batch

MODELS = ['SDU-AI/DeepSeek-V4-Flash', 'Ali-dashscope/Qwen3.5-Flash']
QUESTIONS = {
    'T02': '花窗一淋雨不会糊掉吧？',
    'T03': '我想知道材质，布是什么，骨架是什么？',
    'T05': '单面和双面到底差在哪？',
    'T06': '单面内花窗有自动款吗？',
    'T08': '单面、双面分别多少钱？',
    'T09': '自动和手动有什么不同？',
    'T11': '不会收，有教程吗？',
    'T12': '地址还没截止，在哪改？要找你们吗？',
    'T13': '二团模拟日期是 5 月 8 日，微店显示地址改成功了，算数吗？',
    'T14': '5 月 8 日我搬家了，原地址收不到，请帮我改。',
    'T16': '如果要申请售后，需要准备哪些东西？',
    'T18': '我用了两天，按按钮打不开，是我不会用还是质量问题？',
    'T20': '说不清，你帮我看看。',
}

# 固定的构造历史，不是本轮模型实际生成；两模型看到相同消息。
# 只检验给定上下文后的下一条回复，不代表完成端到端多轮验收。
HISTORIES = {
    'T11': [
        {'role': 'user', 'content': '我买的是自动款。'},
        {'role': 'assistant', 'content': '好的，已了解是自动款。'},
    ],
    'T20': [
        {'role': 'user', 'content': '我买的是自动款，使用时有问题。'},
        {'role': 'assistant', 'content': '你能描述一下问题表现吗？'},
        {'role': 'user', 'content': '不知道。'},
        {'role': 'assistant', 'content': '可以说明是在打开还是收回时遇到困难吗？'},
    ],
}


if __name__ == '__main__':
    run_batch(MODELS, QUESTIONS, label='extended-dev', histories=HISTORIES)
