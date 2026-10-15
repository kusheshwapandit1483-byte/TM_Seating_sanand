"""Regression checks against the exact user-supplied production sketch."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent
BASE = (ROOT / 'reference_original.txt').read_text(encoding='utf-8-sig')
NEW = (ROOT / 'cell_6.ino').read_text(encoding='utf-8-sig')

def functions(source):
    result = {}
    signatures = re.finditer(r'(?m)^(?:void|int|bool)\s+(\w+)\([^;{}]*\)\s*\{', source)
    # Ignore braces inside strings and comments when identifying a function's end.
    tokens = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*[\s\S]*?\*/|[{}]')
    for signature in signatures:
        depth = 1
        for token in tokens.finditer(source, signature.end()):
            if token.group() == '{':
                depth += 1
            elif token.group() == '}':
                depth -= 1
                if depth == 0:
                    result[signature.group(1)] = source[signature.start():token.end()]
                    break
    return result


class Cell6PreservationTests(unittest.TestCase):
    def test_no_original_function_removed_and_only_allowed_functions_changed(self):
        original, updated = functions(BASE), functions(NEW)
        self.assertGreater(len(original), 30)
        self.assertTrue(original.keys() <= updated.keys())
        allowed = {
            'addCamera', 'recomputeOccupancyFromCameraTotals', 'resetSessionCountsForVacancy',
            'applyOutputs', 'refreshRunOutputs', 'updateRunOutputTimers', 'occupancyRelayActive',
            'onMqttMessage', 'clearBootCounts', 'handleClearCountHold', 'runAggregation',
            'runWatchdog', 'publishSystemStatus', 'publishDiagnostics', 'printSerialMonitor', 'setup',
        }
        for name, body in original.items():
            if name not in allowed:
                self.assertEqual(body, updated[name], name + ' must remain unchanged')

    def test_all_existing_defines_except_firmware_version_preserved(self):
        original = dict(re.findall(r'(?m)^#define\s+(\w+)\s+([^\n]+)', BASE))
        updated = dict(re.findall(r'(?m)^#define\s+(\w+)\s+([^\n]+)', NEW))
        for name, value in original.items():
            if name != 'FW_VERSION':
                self.assertEqual(value, updated[name], name)

    def test_max_entry_max_exit_and_every_original_output_helper_preserved(self):
        original, updated = functions(BASE), functions(NEW)
        for name in ('buildDoorTotals', 'computeProcessedTotals', 'setRunOccupiedOutputs',
                     'setRunEmptyOutputs', 'setRunClearingOutputs', 'setMaintenanceOutputs',
                     'handleButtons', 'handleStartPress', 'handleMaintenanceHold',
                     'checkEthernetRecovery', 'onEthEvent', 'exportLog', 'loadCounts',
                     'saveCounts', 'printStatusTable', 'checkSerialCommands', 'loop'):
            self.assertEqual(original[name], updated[name], name)

    def test_camera_roles_are_explicit_and_unassigned(self):
        # No guess about the physical inside camera is baked into production source.
        self.assertIn('#define CELL_6_INSIDE_CAMERA_ID  ""', NEW)
        self.assertIn('#define CELL_6_OUTSIDE_CAMERA_ID ""', NEW)
        self.assertIn('cameraRolesValid = validateCell6CameraRoles();', NEW)
        self.assertIn('bool anyFault = !cameraRolesValid;', NEW)

    def test_companion_flow_protocol_is_compatible(self):
        import json
        flow = json.loads((ROOT / 'recamera/production_flow.json').read_text(encoding='utf-8'))
        node = next(n for n in flow if n['id'] == 'prod_presence')
        for field in ('state', 'valid', 'present', 'confirmed_ms', 'frame_age_ms'):
            self.assertIn('doc["' + field + '"]', NEW)
            self.assertRegex(node['func'], r'\b' + field + r'\s*[:,]')
        self.assertIn('topic: \'eagle/\' + deviceId + \'/presence\'', node['func'])
        self.assertIn('mqttBroker.subscribe("eagle/+/presence"', NEW)


if __name__ == '__main__':
    unittest.main()
