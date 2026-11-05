// Copy to config.js for local static hosting. This public/publishable key is
// designed for browser use; never put a service_role/secret key here.
window.STREAMLINE_CONFIG = {
  supabaseUrl: 'https://YOUR-PROJECT.supabase.co',
  supabaseAnonKey: 'YOUR_SUPABASE_PUBLISHABLE_OR_ANON_KEY',
  // Free Supabase projects have a fixed 50 MB per-file limit.
  maxUploadBytes: 50 * 1024 * 1024,
  // Optional if you use a non-standard Storage hostname:
  // tusEndpoint: 'https://YOUR-PROJECT.storage.supabase.co/storage/v1/upload/resumable'
};
