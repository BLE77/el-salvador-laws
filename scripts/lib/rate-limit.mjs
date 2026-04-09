/**
 * Simple rate limiter and retry wrapper.
 */

/** Sleep for ms milliseconds. */
export function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * Create a rate-limited fetch-like function.
 * @param {number} minDelayMs - Minimum delay between requests.
 */
export function createThrottle(minDelayMs = 1500) {
  let lastRequest = 0;

  return async function throttle() {
    const now = Date.now();
    const elapsed = now - lastRequest;
    if (elapsed < minDelayMs) {
      await sleep(minDelayMs - elapsed);
    }
    lastRequest = Date.now();
  };
}

/**
 * Retry an async function with exponential backoff.
 * @param {Function} fn - Async function to retry.
 * @param {number} maxRetries - Maximum number of retries.
 * @param {number} baseDelay - Base delay in ms.
 */
export async function withRetry(fn, maxRetries = 3, baseDelay = 2000) {
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      return await fn();
    } catch (err) {
      if (attempt === maxRetries) throw err;
      const delay = baseDelay * Math.pow(2, attempt) + Math.random() * 1000;
      console.log(`  [retry] Attempt ${attempt + 1} failed: ${err.message}. Retrying in ${Math.round(delay)}ms...`);
      await sleep(delay);
    }
  }
}
