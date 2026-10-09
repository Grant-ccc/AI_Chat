"""关键开发题重复测试：四题×两模型×两次；默认不联网。"""
from batch_test import run_batch, QUESTIONS as CORE_QUESTIONS
from extended_test import MODELS, QUESTIONS as EXTENDED_QUESTIONS, HISTORIES

QUESTIONS = {
    'T07': CORE_QUESTIONS['T07'],
    'T10': CORE_QUESTIONS['T10'],
    'T11': EXTENDED_QUESTIONS['T11'],
    'T13': EXTENDED_QUESTIONS['T13'],
}


if __name__ == '__main__':
    run_batch(MODELS, QUESTIONS, label='repeat-dev',
              histories={'T11': HISTORIES['T11']}, repetitions=2)
