# Design

Keep transaction handling inside each existing writer critical section. Include commit in the protected try. On BaseException, attempt rollback before unlock; preserve the original exception if rollback also fails. Classify only sqlite3.OperationalError with numeric sqlite_errorcode whose low byte equals SQLITE_BUSY. Raise WriterLockBusy with reason sqlite-busy from the original error. Other errors propagate unchanged. Shared classification does not change writer_lock context manager behavior. Diagnostics use the existing bounded formatter, never raw SQLite exception text.
