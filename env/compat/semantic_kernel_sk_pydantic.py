# Copyright (c) Microsoft. All rights reserved. Licensed under the MIT License.
# Compatibility adjustment retained from the verified 75 deployment.
import sys

if sys.version_info >= (3, 9):
    from typing import Annotated
else:
    from typing_extensions import Annotated

from pydantic import BaseModel, ConfigDict, UrlConstraints
from pydantic import AnyUrl as Url

HttpsUrl = Annotated[Url, UrlConstraints(max_length=2083, allowed_schemes=["https"])]


class SKBaseModel(BaseModel):
    """Base class for all pydantic models in the SK."""

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)


PydanticField = SKBaseModel
SKGenericModel = SKBaseModel
