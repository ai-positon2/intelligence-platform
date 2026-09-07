"""Subprocess fault probe used only by disposable native PostgreSQL tests.

No provider SDK is called. The pause lets the parent kill a real worker process
at known persistence boundaries and test what a replacement worker reuses.
"""
import os
import sys
from tracker import event_intel_jobs as J, event_intel_pipeline as P
from tracker import event_intel_store as S


def pause_at(boundary):
    if os.getenv('EVI_PROBE_BOUNDARY') == boundary:
        print('PROBE_READY', flush=True)
        sys.stdin.read()
        raise RuntimeError('Probe unexpectedly resumed instead of being killed')


def research():
    call = J.reserve_call('probe system', 'probe input', 'offline-test', 10, 0)
    if call['cached'] is not None:
        return call['cached']
    pause_at('before_response')
    result = {'name': 'Recovered probe event', 'usage': {'output_tokens': 1}}
    J.finish_call(call['id'], result, 1)
    pause_at('after_response')
    return result


def pipeline(run_id, *args, **kwargs):
    result = J.stage('probe-research', research)
    pause_at('after_stage')
    assert S.save_event(run_id, result)
    S.update_run(run_id, status='complete', summary={'probe': 'recovered'})


if __name__ == '__main__':
    P.run_job = pipeline
    assert J.run_once()
