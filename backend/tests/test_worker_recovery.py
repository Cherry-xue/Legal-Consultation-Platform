"""A temporary database outage must not stop the queue consumer."""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.exc import OperationalError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import worker


def unavailable():
    return OperationalError('test query', {}, Exception('offline'))


class WorkerRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_reconnects_and_resumes_polling(self):
        sleep = AsyncMock(side_effect=[None, asyncio.CancelledError()])
        with patch.object(worker, 'init_db', side_effect=[unavailable(), None]) as init, \
                patch.object(worker, 'session_factory', return_value=MagicMock()), \
                patch.object(worker, 'claim', return_value=None) as claim, \
                patch.object(worker.asyncio, 'sleep', sleep):
            with self.assertRaises(asyncio.CancelledError):
                await worker.run()
        self.assertEqual(init.call_count, 2)
        claim.assert_called_once()
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [5, 2])

    async def test_polling_reconnects_and_processes_next_job(self):
        sleep = AsyncMock(side_effect=[None, asyncio.CancelledError()])
        process = AsyncMock()
        with patch.object(worker, 'init_db'), \
                patch.object(worker, 'session_factory', return_value=MagicMock()), \
                patch.object(worker, 'claim', side_effect=[unavailable(), 'job-1', None]), \
                patch.object(worker, 'process', process), \
                patch.object(worker.asyncio, 'sleep', sleep):
            with self.assertRaises(asyncio.CancelledError):
                await worker.run()
        process.assert_awaited_once_with('job-1')

    async def test_failure_save_outage_does_not_exit_consumer(self):
        factory = MagicMock()
        factory.return_value.return_value.__enter__.return_value.get.side_effect = unavailable()
        sleep = AsyncMock(side_effect=[None, asyncio.CancelledError()])
        with patch.object(worker, 'init_db'), \
                patch.object(worker, 'session_factory', factory), \
                patch.object(worker, 'claim', side_effect=['job-1', None]) as claim, \
                patch.object(worker, 'process', AsyncMock(side_effect=RuntimeError('processing failed'))), \
                patch.object(worker.asyncio, 'sleep', sleep):
            with self.assertRaises(asyncio.CancelledError):
                await worker.run()
        self.assertEqual(claim.call_count, 2)


if __name__ == '__main__':
    unittest.main()
