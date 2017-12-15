""" Contains Batch classes for images """

import os   # pylint: disable=unused-import
import traceback

import numpy as np
try:
    import blosc   # pylint: disable=unused-import
except ImportError:
    pass
try:
    import PIL.Image
except ImportError:
    pass
try:
    import scipy.ndimage
except ImportError:
    pass
# try:
#     from numba import njit
# except ImportError:
#     from .decorators import njit

from .batch import Batch
from .decorators import action, inbatch_parallel, any_action_failed

# @njit(nogil=True)
# def calc_coords(image_coord, shape_coord, crop):
#     """ Return coords for preserve_shape """
#     if image_coord < shape_coord:
#         new_image_origin = calc_origin(image_coord, shape_coord, crop)
#         image_origin = 0
#         image_len = image_coord
#     else:
#         new_image_origin = 0
#         image_origin = calc_origin(image_coord, shape_coord, crop)
#         image_len = shape_coord
#     return image_origin, new_image_origin, image_len

# @njit(nogil=True)
# def preserve_shape_numba(image_shape, shape, crop='center'):
#     """ Change the image shape by cropping and adding empty pixels to fit the given shape """
#     x, new_x, len_x = calc_coords(image_shape[0], shape[0], crop)
#     y, new_y, len_y = calc_coords(image_shape[1], shape[1], crop)
#     new_x = new_x, new_x + len_x
#     x = x, x + len_x
#     new_y = new_y, new_y + len_y
#     y = y, y + len_y
#     return new_x, new_y, x, y



class BaseImagesBatch(Batch):
    """ Batch class for 2D images """
    components = "images", "labels"

    @property
    def image_shape(self):
        """: tuple - shape of the images """
        return self.images.shape[1:]

    def assemble(self, all_res, *args, **kwargs):
        """ Assemble the batch after a parallel action """
        _ = all_res, args, kwargs
        raise NotImplementedError("Must be implemented in a child class")

    def _assemble_load(self, all_res, *args, **kwargs):
        """ Build the batch data after loading data from files """
        _ = all_res, args, kwargs
        return self

    @action
    def load(self, src=None, fmt=None, components=None, *args, **kwargs):
        """ Load data """
        return super().load(src, fmt, components, *args, **kwargs)

    @action
    def dump(self, dst=None, fmt=None, components=None, *args, **kwargs):
        """ Save data to a file or a memory object """
        return super().dump(dst, fmt, components=None, *args, **kwargs)

    @staticmethod
    def get_image_shape(image):
        """ Return an image size (rows, columns) """
        raise NotImplementedError("must be implemented in child classes")

    @action
    @inbatch_parallel(init='indices', post='assemble')
    def crop(self, ix, components='images', origin='top_left', shape=None):
        """ Crop all images in the batch

        Parameters
        ----------
        components : str
            name of the component to be cropped

        origin : tuple
            can be one of:

            - tuple - a starting point in the form of (row, column)
            - 'top_left' - crop from left top edge (0,0)
            - 'center' - crop from center of each image
            - 'random' - crop at random position

        shape : tuple
            crop size in the form of (rows, columns)
        """

        if isinstance(origin, str) and origin not in ['top_left', 'center', 'random']:
            raise ValueError('origin must be either in [\'top_left\', \'center\', \'random\'] or be a tuple')

        if shape is None:
            raise ValueError('shape must be specified')

        image = self.get(ix, components)


        if origin == 'random':

            origin = (np.random.randint(image.shape[0]-shape[0]+1),
                      np.random.randint(image.shape[1]-shape[1]+1))
        return self._crop_image(image, origin, shape)

    @action
    @inbatch_parallel(init='indices', post='assemble')
    def resize(self, ix, components='images', shape=(64, 64)):
        """ Resize all images in the batch to the given shape

        Parameters
        ----------
        components : str
            name of the component to be resized

        shape : tuple
            crop size in the form of (width, height)
        """
        return self._resize_image(self.get(ix, components), shape)

    @staticmethod
    def _preserve_shape(original_image, rescaled_image, origin='center'):
        """ Change the image shape by cropping and adding empty pixels to fit the given shape """
        raise NotImplementedError()

    @action
    @inbatch_parallel(init='indices', post='assemble')
    def scale(self, ix, components='images', p=1., factor=None, preserve_shape=True, origin='center'):
        """ Scale the content of each image in the batch

        Parameters
        -----------
        components : str
            name of the component to be scaled

        p : float
            probability of applying scale to an element of the batch
            (0. - don't scale, .5 - scale half of images, 1 - scale all images)

        factor : (tuple_1, tuple_2) or tuple
            (1) if tuple is passed then one factor is sampled from U(tuple) and it is applied to all axes
            (2) if (tuple_1, tuple_2) is passed then sampling and scaling are applied to each axis separately

        preserve_shape : bool
            whether to preserve the shape of the image after scaling

        origin : {'center', 'top_left', tuple}
            origin of the rescaled image on the black background (relevant if `factor` < 1 and `preserve_shape` is True)
        """
        if p > 1 or p < 0:
            raise ValueError("probability must be in [0,1]")
        if np.any(np.asarray(factor) <= 0):
            raise ValueError("factor must be greater than 0")
        if isinstance(origin, str) and origin not in ['top_left', 'center']:
            raise ValueError('origin must be either in [\'top_left\', \'center\'] or be a tuple')

        image = self.get(ix, components)
        if np.random.random() < p:
            if isinstance(factor[0], tuple):
                _factor = np.random.uniform((factor[0][0], factor[1][0]),
                                            (factor[0][1], factor[1][1]))
            else:
                _factor = np.random.uniform(*factor)

            image_shape = self.get_image_shape(image)
            rescaled_shape = np.round(np.array(image_shape) * _factor).astype(np.int16)
            rescaled_image = self._resize_image(image, rescaled_shape)
            if preserve_shape:
                rescaled_image = self._preserve_shape(image, rescaled_image, origin)
        return rescaled_image

    @action
    @inbatch_parallel(init='indices', post='assemble')
    def rotate(self, ix, components='images', p=1., angle=None, **kwargs):
        """ Rotate each image in the batch at a random angle

        Parameters
        -----------
        components : str
            name of the component to be rotated

        p : float
            probability of applying this action to an image
            (0. - don't rotate, .5 - rotate half of images, 1 - rotate all images)

        angle : tuple or float
            angle's range to sample from in the form of (min_angle, max_angle), in degrees.
            if float is passed than determenistic rotation will be perfomed
        """
        if p > 1 or p < 0:
            raise ValueError("probability must be in [0,1]")

        image = self.get(ix, components)
        if np.random.random() < p:
            angle = angle or (-45., 45.)
            if not isinstance(angle, (float, int)):
                angle = np.random.uniform(*(angle))
            preserve_shape = kwargs.pop('preserve_shape', True)
            return self._rotate_image(image, angle, preserve_shape=preserve_shape, **kwargs)
        return image


    @action
    @inbatch_parallel(init='indices', post='assemble')
    def flip(self, ix, components='images', mode='lr', p=1, p_lr=0.5):
        """ Flip images

        Parameters
        ----------
        components : str
            name of the component to be flipped
        mode : {'lr', 'ud', 'all'}
            'lr' for a left/right flip
            'ud' for an upside down flip
            'all' for one of the two previous ones (probability of choosing 'lr' is specified by `p_lr`)
        p : float
            probability of doing this action
        p_lr : float
            probability of choosing 'lr' (relevant if 'all' is passed to `mode`)
        """

        if p > 1 or p < 0 or p_lr < 0 or p_lr > 1:
            raise ValueError("probability must be in [0,1]")
        if mode not in ['lr', 'ud', 'all']:
            raise ValueError("`mode` must be one of 'lr', 'ud' and 'all'")

        image = self.get(ix, components)
        if np.random.random() < p:
            if mode == 'all':
                return self._flip_image(image, 'lr' if np.random.random() < p_lr else 'ud')
            return self._flip_image(image, mode)
        return image


    @action
    @inbatch_parallel(init='indices', post='assemble')
    def pad(self, ix, components='images', **kwargs):
        """ pad image

        Parameters
        ----------
        padding : (left, right, top, bottom) in pixels
        """
        return self._pad(self.get(ix, components), **kwargs)

    @action
    @inbatch_parallel(init='indices', post='assemble')
    def invert(self, ix, components='images', p_channel=1):
        """ invert channels

        Parameters
        ----------
        p_channel : (first channel invert prob, second channel invert prob, ...) or float
                    the probabilities of inverting channels. If float is given,
                    then it's decided whether to invert all channels,
                    otherwise each channel will be inverted
                    with the given probability (or won't, if there is no probability)
        """
        p_channel = np.asarray(p_channel).reshape(-1)
        if np.any((np.asarray(p_channel) > 1) + (np.asarray(p_channel) < 0)):
            raise ValueError("probability must be in [0,1]")

        channels = ()
        image = self.get(ix, components)
        if len(p_channel) == 1:
            if np.any(np.random.random() < p_channel):
                channels = list(range(image.shape[-1]))
        else:
            channels = np.where(np.random.uniform(size=len(p_channel)) > p_channel)[0]

        if len(channels) > 0:
            return self._invert(image, channels)
        return image

    @action
    def normalize(self, components='images'):
        """divide each pixel intencity by 255"""

        setattr(self, components, self.get(None, components) / 255.)
        return self



#------------------------------------------------------------------------------------------------


class ImagesBatch(BaseImagesBatch):
    """ Batch class for 2D images

    images are stored as numpy arrays (N, H, W) or (N, H, W, C)

    """
    @staticmethod
    def get_image_shape(image):
        """ Return image shape (rows, columns) """
        return image.shape[:2]

    def assemble_component(self, all_res, components='images'):
        """ Assemble one component """
        try:
            new_images = np.stack(all_res)
        except ValueError as e:
            message = str(e)
            if "must have the same shape" in message:
                min_shape = np.array([x.shape for x in all_res]).min(axis=0)
                all_res = [arr[:min_shape[0], :min_shape[1]].copy() for arr in all_res]
                new_images = np.stack(all_res)
        setattr(self, components, new_images)

    def assemble(self, all_res, *args, **kwargs):
        """ Assemble the batch after a parallel action """
        _ = args, kwargs
        if any_action_failed(all_res):
            all_errors = self.get_errors(all_res)
            print(all_errors)
            traceback.print_tb(all_errors[0].__traceback__)
            raise RuntimeError("Could not assemble the batch")

        components = kwargs.get('components', 'images')
        if isinstance(components, (list, tuple)):
            all_res = list(zip(*all_res))
        else:
            components = [components]
            all_res = [all_res]
        for component, res in zip(components, all_res):
            self.assemble_component(res, component)
        return self

    # @action
    # def convert_to_pil(self, components='images'):
    #     """ Convert batch data to PIL.Image format """
    #     if self.images is None:
    #         new_images = None
    #     else:
    #         new_images = np.asarray(list(None for _ in self.indices))
    #         self.apply_transform(new_images, components, PIL.Image.fromarray)
    #     new_data = (new_images, self.labels)
    #     new_batch = ImagesPILBatch(np.arange(len(self)), preloaded=new_data)
    #     return new_batch

    @staticmethod
    def _calc_origin(image_shape, origin, shape):
        if origin == 'top_left':
            origin = 0, 0
        elif origin == 'center':
            origin = np.maximum(0, image_shape - np.asarray(shape)) // 2
        return origin


    @staticmethod
    def _preserve_shape(original_image, rescaled_image, origin='center'):
        """ Change the image shape by cropping and/or adding empty pixels to fit the given shape """
        new_image = np.zeros(original_image.shape, dtype=np.uint8)
        rescaled_shape = rescaled_image.shape[:2]
        rescaled_image = ImagesBatch._crop_image(rescaled_image, origin, new_image.shape[:2])
        origin = ImagesBatch._calc_origin(new_image.shape[:2], origin, rescaled_shape)
        slice_rows = slice(origin[0], origin[0]+rescaled_image.shape[0])
        slice_columns = slice(origin[1], origin[1]+rescaled_image.shape[1])
        new_image[slice_rows, slice_columns] = rescaled_image
        return new_image


    @staticmethod
    def _resize_image(image, shape=None):
        """ Resize an image """
        factor = np.asarray(shape) / np.asarray(image.shape[:2])
        if len(image.shape) > 2:
            factor = np.concatenate((factor, [1.] * len(image.shape[2:])))
        new_image = scipy.ndimage.interpolation.zoom(image, factor, order=3)
        return new_image

    @staticmethod
    def _rotate_image(image, angle, preserve_shape, **kwargs):
        """ Rotate an image """
        kwargs['reshape'] = not preserve_shape
        new_image = scipy.ndimage.interpolation.rotate(image, angle, **kwargs)
        return new_image

    @staticmethod
    def _crop_image(image, origin, shape):
        origin = ImagesBatch._calc_origin(image.shape[:2], origin, shape)

        if np.all(origin + shape > image.shape[:2]):
            shape = image.shape[:2] - origin

        row_slice = slice(origin[0], origin[0] + shape[0])
        column_slice = slice(origin[1], origin[1] + shape[1])
        return image[row_slice, column_slice].copy()

    @staticmethod
    def _flip_image(image, mode):
        if mode == 'lr':
            return image[:, ::-1]
        elif mode == 'ud':
            return image[::-1]

    @staticmethod
    def _pad(image, **kwargs):
        return np.pad(image, **kwargs)

    @staticmethod
    def _invert(image, channels):
        inv_multiplier = np.zeros(image.shape[-1], dtype=np.int16)
        inv_multiplier[np.asarray(channels)] = 255
        return np.abs(np.ones(image.shape, dtype=np.int16)*inv_multiplier - image.astype(np.int16)).astype(np.uint8)



#------------------------------------------------------------------------------------------------


class ImagesPILBatch(BaseImagesBatch):
    """ Batch class for 2D images in PIL format """
    @staticmethod
    def get_image_shape(image):
        """ Return image size (width, height) """
        return image.size

    def assemble(self, all_res, *args, **kwargs):
        """ Assemble the batch after a parallel action """
        _ = args, kwargs
        if any_action_failed(all_res):
            all_errors = self.get_errors(all_res)
            print(all_errors)
            traceback.print_tb(all_errors[0].__traceback__)
            raise RuntimeError("Could not assemble the batch")

        components = kwargs.get('components', 'images')
        if isinstance(components, (list, tuple)):
            all_res = list(zip(*all_res))
        else:
            components = [components]
            all_res = [all_res]
        for component, res in zip(components, all_res):
            new_data = np.array(res, dtype='object')
            setattr(self, component, new_data)
        return self

    @action
    def convert_to_array(self, dtype=np.uint8):
        """ Convert images from PIL.Image format to an array """
        if self.images is not None:
            new_images = list(None for _ in self.indices)
            self.apply_transform(new_images, 'images', self._convert_to_array_one, dtype=dtype)
            new_images = np.stack(new_images)
        else:
            new_images = None
        new_data = new_images, self.labels
        new_batch = ImagesBatch(np.arange(len(self)), preloaded=new_data)
        return new_batch

    def _convert_to_array_one(self, image, dtype=np.uint8):
        if image is not None:
            arr = np.fromstring(image.tobytes(), dtype=dtype)
            if image.palette is None:
                new_shape = (image.height, image.width)
            else:
                new_shape = (image.height, image.width, -1)
            new_image = arr.reshape(*new_shape)
        else:
            new_image = None
        return new_image

    def _resize_image(self, ix, components='images', shape=None, **kwargs):
        """ Resize one image """
        _ = kwargs
        image = self.get(ix, components)
        new_image = image.resize(shape, PIL.Image.ANTIALIAS)
        return new_image

    def _preserve_shape(self, image, shape, crop='center'):
        new_x, new_y, x, y = preserve_shape_numba(image.size, shape, crop)
        new_image = PIL.Image.new(image.mode, shape)
        box = x[0], y[0], x[1], y[1]
        new_image.paste(image.crop(box), (new_x[0], new_y[0]))
        return new_image

    def _rotate_image(self, ix, components='images', angle=0, preserve_shape=True, **kwargs):
        """ Rotate one image """
        image = self.get(ix, components)
        kwargs['expand'] = not preserve_shape
        new_image = image.rotate(angle, **kwargs)
        return new_image

    def _crop_image(self, image, origin, shape):
        """ Crop one image """
        if origin is None or origin == 'top_left':
            origin = 0, 0
        elif origin == 'center':
            origin = (image.width - shape[0]) // 2, (image.height - shape[1]) // 2
        origin_x, origin_y = origin
        shape = shape if shape is not None else (image.width - origin_x, image.height - origin_y)
        box = origin_x, origin_y, origin_x + shape[0], origin_y + shape[1]
        new_image = image.crop(box)
        return new_image

    @inbatch_parallel('indices', post='assemble')
    def _crop(self, ix, components='images', origin=None, shape=None):
        """ Crop all images """
        image = self.get(ix, components)
        new_image = self._crop_image(image, origin, shape)
        return new_image

    @inbatch_parallel('indices', post='assemble')
    def _random_crop(self, ix, components='images', shape=None):
        """ Crop all images with a given shape and a random origin """
        image = self.get(ix, components)
        origin_x = np.random.randint(0, image.width - shape[0])
        origin_y = np.random.randint(0, image.height - shape[1])
        new_image = self._crop_image(image, (origin_x, origin_y), shape)
        return new_image

    @action
    @inbatch_parallel('indices', post='assemble')
    def fliplr(self, ix, components='images'):
        """ Flip image horizontaly (left / right) """
        image = self.get(ix, components)
        return image.transpose(PIL.Image.FLIP_LEFT_RIGHT)

    @action
    @inbatch_parallel('indices', post='assemble')
    def flipud(self, ix, components='images'):
        """ Flip image verticaly (up / down) """
        image = self.get(ix, components)
        return image.transpose(PIL.Image.FLIP_TOP_BOTTOM)
