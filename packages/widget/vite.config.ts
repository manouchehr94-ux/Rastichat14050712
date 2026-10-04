import { defineConfig } from 'vite';

export default defineConfig({
    server: {
        port: 3000
    },
    build: {
        outDir: 'dist',
        lib: {
            entry: 'src/main.ts',
            name: 'RastiChat',
            fileName: 'widget',
            formats: ['iife']
        }
    }
});
