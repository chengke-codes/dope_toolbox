import tempfile
import os
import random
import time
from glob import glob
import threading
import numpy as np
import nvisii
from numpy import deg2rad
from squaternion import Quaternion
import pybullet as p
from nvisii import vec3
import simplejson as json
import cv2
from typing import Union, Optional
from pyquaternion import Quaternion as pyq


class FixRotation:
    _latest_img: Optional[Union[np.ndarray, bool]]

    def __init__(self):
        self._base_path = os.path.dirname(os.path.abspath(__file__))
        self._spp = 200
        self._width = 400
        self._height = 400
        self._steps = 60
        self._objects_per_img = 20
        self._hdr_paths = glob(os.path.join(self._base_path, "hdr", "*.hdr"))
        print("found %d hdr files" % len(self._hdr_paths))

        self.models = {
            'ycb_002_master_chef_can': os.path.join(
                self._base_path,
                "models/ycb_002_master_chef_can/meshes/textured_fix.obj"
            )
        }

        if len(self.models) == 0:
            raise RuntimeError("no available models found")

        # http://learnwebgl.brown37.net/07_cameras/camera_introduction.html
        self._camera_look_at = {
            'at': (0, 0, 0),
            'up': (0, 1, 1),
            'eye': (0, 0, 0.8)
        }
        self._pbt_client = None

    @staticmethod
    def make_location():
        return (
            random.uniform(-0.25, 0.25),
            random.uniform(-0.25, 0.25),
            random.uniform(0.0, 0.3)
        )

    @staticmethod
    def make_rotation():
        new_rot = (
            random.uniform(-np.pi, np.pi),
            random.uniform(-np.pi, np.pi),
            random.uniform(-np.pi, np.pi),
        )
        q = Quaternion.from_euler(*new_rot)
        return q.x, q.y, q.z, q.w

    @staticmethod
    def is_valid_json(json_path):
        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
            return isinstance(data, dict)
        except ValueError:
            return False

    def _generate_one(self):
        nvisii.clear_all()
        p.resetSimulation()
        camera = nvisii.entity.create(
            name="camera",
            transform=nvisii.transform.create("camera"),
            camera=nvisii.camera.create(
                name="camera",
                aspect=float(self._width) / float(self._height)
            )
        )
        camera.get_transform().look_at(**self._camera_look_at)
        nvisii.set_camera_entity(camera)

        dome = nvisii.texture.create_from_file("dome", random.choice(self._hdr_paths))
        nvisii.set_dome_light_texture(dome)
        nvisii.set_dome_light_rotation(nvisii.angleAxis(deg2rad(random.random() * 720), vec3(0, 0, 1)))

        obj_model_map = {}
        pbt_obj_map = []
        for _ in range(self._objects_per_img):
            obj_class_name = random.choice(list(self.models.keys()))
            obj_path = self.models[obj_class_name]

            scene = nvisii.import_scene(file_path=obj_path)
            obj = scene.entities[0]
            assert isinstance(obj, nvisii.entity)
            obj_name = obj.get_name()
            obj_model_map[obj_name] = obj_class_name

            pose = self.make_location()
            obj.get_transform().set_position(pose)
            rot = self.make_rotation()
            obj.get_transform().set_rotation(rot)

            vertices = obj.get_mesh().get_vertices()

            obj_col_id = p.createCollisionShape(
                p.GEOM_MESH,
                vertices=vertices,
            )

            p.createMultiBody(
                baseCollisionShapeIndex=obj_col_id,
                basePosition=pose,
                baseOrientation=rot,
                baseMass=0.01
            )

            pbt_obj_map.append({
                'pbt': obj_col_id,
                'nvi': obj_name
            })

        self._run_physics_engine(pbt_obj_map)

        ori_img_data = self.nvisii_to_cv()
        cv2.imshow("ori", ori_img_data)

        def round_4(yaw_pitch_roll):
            return tuple(map(lambda x: round(x, 4), yaw_pitch_roll))

        for _map in pbt_obj_map:
            entity = nvisii.entity.get(_map['nvi'])
            trans = entity.get_transform()
            q = self.nvi_q_to_pyq(trans.get_rotation())
            yaw, pitch, roll = q.yaw_pitch_roll
            trans.add_angle_axis(angle=0 - yaw, axis=nvisii.vec3(0, 0, 1))
            print(round_4(q.yaw_pitch_roll), round_4(self.nvi_q_to_pyq(trans.get_rotation()).yaw_pitch_roll))

        fix_img_data = self.nvisii_to_cv()
        cv2.imshow("fix", fix_img_data)
        cv2.waitKey(0)

    @staticmethod
    def nvi_q_to_pyq(_nvi_quat):
        return pyq(w=_nvi_quat.w, x=_nvi_quat.x, y=_nvi_quat.y, z=_nvi_quat.z)

    def nvisii_to_cv(self):
        fd, save_path = tempfile.mkstemp(prefix="lasr_dope_", suffix=".png")
        nvisii.render_to_file(
            width=self._width,
            height=self._height,
            samples_per_pixel=self._spp,
            file_path=save_path
        )
        os.close(fd)
        img_data = cv2.imread(save_path)
        os.unlink(save_path)
        return img_data

    @staticmethod
    def _get_cuboid_image_space(obj_name):
        cam_matrix = nvisii.entity.get('camera').get_transform().get_world_to_local_matrix()
        cam_proj_matrix = nvisii.entity.get('camera').get_camera().get_projection()

        points = []
        points_cam = []
        for i_t in range(9):
            trans = nvisii.transform.get(f"{obj_name}_cuboid_{i_t}")
            mat_trans = trans.get_local_to_world_matrix()
            pos_m = nvisii.vec4(
                mat_trans[3][0],
                mat_trans[3][1],
                mat_trans[3][2],
                1)

            p_cam = cam_matrix * pos_m

            p_image = cam_proj_matrix * (cam_matrix * pos_m)
            p_image = nvisii.vec2(p_image) / p_image.w
            p_image = p_image * nvisii.vec2(1, -1)
            p_image = (p_image + nvisii.vec2(1, 1)) * 0.5

            points.append([p_image[0], p_image[1]])
            points_cam.append([p_cam[0], p_cam[1], p_cam[2]])

        return points, points_cam

    def _export_json(self, save_path, obj_names, obj_model_map, visibility_use_percentage=False):
        camera_entity = nvisii.entity.get("camera")
        camera_trans = camera_entity.get_transform()
        # assume we only use the view camera
        cam_matrix = camera_trans.get_world_to_local_matrix()

        cam_matrix_export = []
        for row in cam_matrix:
            cam_matrix_export.append([row[0], row[1], row[2], row[3]])

        cam_world_location = camera_trans.get_position()
        cam_world_quaternion = camera_trans.get_rotation()

        cam_intrinsics = camera_entity.get_camera().get_intrinsic_matrix(self._width, self._height)
        dict_out = {
            "camera_data": {
                "width": self._width,
                'height': self._height,
                'camera_look_at':
                    {
                        'at': [
                            self._camera_look_at['at'][0],
                            self._camera_look_at['at'][1],
                            self._camera_look_at['at'][2],
                        ],
                        'eye': [
                            self._camera_look_at['eye'][0],
                            self._camera_look_at['eye'][1],
                            self._camera_look_at['eye'][2],
                        ],
                        'up': [
                            self._camera_look_at['up'][0],
                            self._camera_look_at['up'][1],
                            self._camera_look_at['up'][2],
                        ]
                    },
                'camera_view_matrix': cam_matrix_export,
                'location_world':
                    [
                        cam_world_location[0],
                        cam_world_location[1],
                        cam_world_location[2],
                    ],
                'quaternion_world_xyzw': [
                    cam_world_quaternion[0],
                    cam_world_quaternion[1],
                    cam_world_quaternion[2],
                    cam_world_quaternion[3],
                ],
                'intrinsics': {
                    'fx': cam_intrinsics[0][0],
                    'fy': cam_intrinsics[1][1],
                    'cx': cam_intrinsics[2][0],
                    'cy': cam_intrinsics[2][1]
                }
            },
            "objects": []
        }

        # Segmentation id to export
        id_keys_map = nvisii.entity.get_name_to_id_map()

        for obj_name in obj_names:
            projected_key_points, _ = self._get_cuboid_image_space(obj_name)

            # put them in the image space.
            for i_p, _p in enumerate(projected_key_points):
                projected_key_points[i_p] = [_p[0] * self._width, _p[1] * self._height]

            # Get the location and rotation of the object in the camera frame

            trans = nvisii.entity.get(obj_name).get_transform()
            quaternion_xyzw = nvisii.inverse(cam_world_quaternion) * trans.get_rotation()

            object_world = nvisii.vec4(
                trans.get_position()[0],
                trans.get_position()[1],
                trans.get_position()[2],
                1
            )
            pos_camera_frame = cam_matrix * object_world

            # check if the object is visible
            bounding_box = [-1, -1, -1, -1]

            seg_mask = nvisii.render_data(
                width=self._width,
                height=self._height,
                start_frame=0,
                frame_count=1,
                bounce=0,
                options="entity_id",
            )
            seg_mask = np.array(seg_mask).reshape((self._width, self._height, 4))[:, :, 0]

            if visibility_use_percentage is True and int(id_keys_map[obj_name]) in np.unique(seg_mask.astype(int)):
                transforms_to_keep = {}

                for _name in id_keys_map.keys():
                    if 'camera' in _name.lower() or obj_name in _name:
                        continue
                    trans_to_keep = nvisii.entity.get(_name).get_transform()
                    transforms_to_keep[_name] = trans_to_keep
                    nvisii.entity.get(_name).clear_transform()

                # Percentage visibility through full segmentation mask.
                seg_unique_mask = nvisii.render_data(
                    width=self._width,
                    height=self._height,
                    start_frame=0,
                    frame_count=1,
                    bounce=0,
                    options="entity_id",
                )

                seg_unique_mask = np.array(seg_unique_mask).reshape((self._width, self._height, 4))[:, :, 0]

                values_segmentation = np.where(seg_mask == int(id_keys_map[obj_name]))[0]
                values_segmentation_full = np.where(seg_unique_mask == int(id_keys_map[obj_name]))[0]
                visibility = len(values_segmentation) / float(len(values_segmentation_full))

                # set back the objects from remove
                for entity_name in transforms_to_keep.keys():
                    nvisii.entity.get(entity_name).set_transform(transforms_to_keep[entity_name])
            else:
                if int(id_keys_map[obj_name]) in np.unique(seg_mask.astype(int)):
                    visibility = 1
                    y, x = np.where(seg_mask == int(id_keys_map[obj_name]))
                    bounding_box = [int(min(x)), int(max(x)), self._height - int(max(y)), self._height - int(min(y))]
                else:
                    visibility = 0

            object_class_name = obj_model_map[obj_name]
            dict_out['objects'].append({
                'class': object_class_name,
                'name': "%s_%d" % (object_class_name, round(time.time() * 1000)),
                'provenance': 'nvisii',
                'location': [
                    pos_camera_frame[0],
                    pos_camera_frame[1],
                    pos_camera_frame[2]
                ],
                'quaternion_xyzw': [
                    quaternion_xyzw[0],
                    quaternion_xyzw[1],
                    quaternion_xyzw[2],
                    quaternion_xyzw[3],
                ],
                'quaternion_xyzw_world': [
                    trans.get_rotation()[0],
                    trans.get_rotation()[1],
                    trans.get_rotation()[2],
                    trans.get_rotation()[3]
                ],
                'projected_cuboid': projected_key_points[0:8],
                'projected_cuboid_centroid': projected_key_points[8],
                # 'segmentation_id': id_keys_map[obj_name],
                'segmentation_id': 0,
                'visibility_image': visibility,
                'bounding_box': {
                    'top_left': [
                        bounding_box[0],
                        bounding_box[2],
                    ],
                    'bottom_right': [
                        bounding_box[1],
                        bounding_box[3],
                    ],
                },
            })

        with open(save_path, 'w+') as fp:
            json.dump(dict_out, fp, indent=4, sort_keys=True)
        return dict_out

    def _run_physics_engine(self, pbt_nvi_map):
        for i in range(self._steps):
            p.stepSimulation()

        for _map in pbt_nvi_map:
            pos, rot = p.getBasePositionAndOrientation(_map['pbt'])
            entity = nvisii.entity.get(_map['nvi'])
            entity.get_transform().set_position(pos)
            entity.get_transform().set_rotation(rot)

    @staticmethod
    def _add_cuboid(entity_name):
        obj = nvisii.entity.get(entity_name)
        min_obj = obj.get_mesh().get_min_aabb_corner()
        max_obj = obj.get_mesh().get_max_aabb_corner()
        centroid_obj = obj.get_mesh().get_aabb_center()
        dimensions_dict = {
            'width': max_obj[0] - min_obj[0],
            'height': max_obj[1] - min_obj[1],
            'length': max_obj[2] - min_obj[2]
        }
        cuboid1 = [
            vec3(max_obj[0], max_obj[1], max_obj[2]),
            vec3(min_obj[0], max_obj[1], max_obj[2]),
            vec3(max_obj[0], min_obj[1], max_obj[2]),
            vec3(max_obj[0], max_obj[1], min_obj[2]),
            vec3(min_obj[0], min_obj[1], max_obj[2]),
            vec3(max_obj[0], min_obj[1], min_obj[2]),
            vec3(min_obj[0], max_obj[1], min_obj[2]),
            vec3(min_obj[0], min_obj[1], min_obj[2]),
            vec3(centroid_obj[0], centroid_obj[1], centroid_obj[2]),
        ]

        cuboid2 = [
            cuboid1[2], cuboid1[0], cuboid1[3],
            cuboid1[5], cuboid1[4], cuboid1[1],
            cuboid1[6], cuboid1[7], cuboid1[-1],
            vec3(centroid_obj[0], centroid_obj[1], centroid_obj[2])
        ]

        for i_p, p in enumerate(cuboid2):
            child_transform = nvisii.transform.create(f"{entity_name}_cuboid_{i_p}")
            child_transform.set_position(p)
            child_transform.set_scale(vec3(0.3))
            child_transform.set_parent(obj.get_transform())

        for i_v, v in enumerate(cuboid2):
            cuboid2[i_v] = [v[0], v[1], v[2]]

        return cuboid2, dimensions_dict

    def run(self):
        nvisii.initialize(headless=True)
        nvisii.enable_denoiser()
        self._pbt_client = p.connect(p.DIRECT)
        p.setGravity(0, 0, -10)

        try:
            while True:
                self._generate_one()
        except KeyboardInterrupt:
            pass

        p.disconnect()
        cv2.destroyAllWindows()
        # let's clean up GPU resources
        nvisii.deinitialize()


if __name__ == '__main__':
    def main():
        _m = FixRotation()
        _m.run()


    main()
