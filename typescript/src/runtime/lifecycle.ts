/** A deadline bounds waiting, not the underlying exporter operation. */
export async function withinBudget(
  operation: () => Promise<void>,
  timeoutMillis: number,
): Promise<boolean> {
  if (!Number.isFinite(timeoutMillis) || timeoutMillis < 0) return false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      Promise.resolve()
        .then(operation)
        .then(
          () => true,
          () => false,
        ),
      new Promise<boolean>((resolve) => {
        timer = setTimeout(() => resolve(false), timeoutMillis);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}
