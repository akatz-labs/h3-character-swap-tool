/** One raster upload contract for both backends and every image picker/drop zone. */
export async function prepareImageUpload(file: File): Promise<File> {
  if (file.size > 25 * 1024 ** 2)
    throw Error('Choose an image under 25 MB.');
  if (!file.type.startsWith('image/') && !/\.(png|jpe?g|webp|gif|avif|bmp|ico|tiff?|heic|heif)$/i.test(file.name))
    throw Error('Choose an image file, such as PNG, JPEG, WebP, GIF or AVIF.');
  let image: ImageBitmap;
  try {
    image = await createImageBitmap(file);
  } catch {
    throw Error('This browser cannot read this image. Try exporting it as PNG, JPEG or WebP.');
  }
  try {
    const scale = Math.min(1, 4096 / image.width, 4096 / image.height);
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(image.width * scale));
    canvas.height = Math.max(1, Math.round(image.height * scale));
    const context = canvas.getContext('2d');
    if (!context) throw Error('Image preparation is unavailable in this browser.');
    // Keep transparency; canvas normalizes the browser-decoded raster and orientation.
    // Animated inputs become a still frame, matching guide/reference semantics.
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    const normalized = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(value => value ? resolve(value) : reject(Error('Could not prepare this image.')), 'image/png'));
    if (normalized.size > 25 * 1024 ** 2)
      throw Error('This image is too large after preparation. Try a smaller image.');
    return new File([normalized], file.name.replace(/\.[^.]+$/, '') + '.png', {type: 'image/png'});
  } finally {
    image.close();
  }
}
