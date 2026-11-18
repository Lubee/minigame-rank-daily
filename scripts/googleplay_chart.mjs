import gplay from 'google-play-scraper';

try {
  const options = {
    country: 'us',
    lang: 'en',
    requestOptions: { timeout: 25000, retry: { limit: 0 } },
  };
  let result;
  if (process.argv[2] === '--app') {
    const appId = process.argv[3];
    if (!/^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$/.test(appId ?? '')) {
      throw new Error('Invalid Google Play package');
    }
    result = await gplay.app({ ...options, appId });
  } else {
    const count = Number(process.argv[2] ?? 100);
    if (!Number.isInteger(count) || count < 1 || count > 100) {
      throw new Error('Expected a chart size between 1 and 100');
    }
    result = await gplay.list({
      ...options, category: gplay.category.GAME,
      collection: gplay.collection.TOP_FREE, num: count, fullDetail: false,
    });
  }
  process.stdout.write(JSON.stringify(result));
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
}
