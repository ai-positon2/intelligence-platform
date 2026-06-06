/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#f0f4ff',
          100: '#dce6ff',
          200: '#baccff',
          300: '#86a8ff',
          400: '#507aff',
          500: '#2b52f5',
          600: '#1a3ae8',
          700: '#1729cc',
          800: '#1926a8',
          900: '#1b2784',
        }
      }
    },
  },
  plugins: [],
}

