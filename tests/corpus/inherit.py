# Inheritance and a polymorphic loop. The subset's classes are structs with no
# vtable, so dynamic dispatch has to become an explicit tag and a branch.


class Shape:
    def __init__(self, name):
        self.name = name

    def area(self):
        return 0

    def describe(self):
        print(self.name, "area", self.area())


class Square(Shape):
    def __init__(self, side):
        super().__init__("square")
        self.side = side

    def area(self):
        return self.side * self.side


class Rect(Shape):
    def __init__(self, w, h):
        super().__init__("rect")
        self.w = w
        self.h = h

    def area(self):
        return self.w * self.h


def main() -> int:
    shapes = [Square(4), Rect(3, 5), Square(7), Rect(2, 9)]
    for shape in shapes:
        shape.describe()
    biggest = max(shapes, key=lambda s: s.area())
    print("biggest is", biggest.name, biggest.area())
    return 0
