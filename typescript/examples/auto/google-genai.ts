import { init } from 'confident-trace';
import { GoogleGenAI } from '@google/genai';

const tracing = init();
try {
  const client = new GoogleGenAI({ apiKey: process.env.GOOGLE_API_KEY! });
  const result = await client.models.generateContent({
    model: process.env.GOOGLE_MODEL!,
    contents: 'Hello',
  });
  console.log(result.text);
} finally {
  await tracing.shutdown();
}
