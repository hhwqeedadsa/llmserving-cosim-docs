import json
import unittest

from moe_ub_cosim.native_control import event


class ControlFrameChecks(unittest.TestCase):
    def test_complete_frame_and_diagnostic(self):
        expected=dict(type='DECIDE',decision_id=0,now_ns=0)
        self.assertEqual(event('MOE_RDMA_EVENT '+json.dumps(expected)+'\n'),expected)
        self.assertIsNone(event('[logger] info\n'))

    def test_corrupt_truncated_oversized_or_wrong_event_rejected(self):
        for frame in ('{}[logger] info\n','{"type":"DECIDE"}',
                      '{"type":"DECIDE","padding":"'+'x'*1048576+'"}\n',
                      '[]\n','{"type":"COMMIT"}\n'):
            with self.subTest(frame=frame[:60]),self.assertRaisesRegex(ValueError,'native control'):
                event('MOE_RDMA_EVENT '+frame)


if __name__=='__main__':unittest.main()
